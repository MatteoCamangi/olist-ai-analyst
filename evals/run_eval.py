"""Run the analyst agent on every question of a split, grade the responses and save the run.

Usage (from the repo root):
    python evals/run_eval.py --split dev --label baseline --agent-cmd "python agent/analyst.py"
    python evals/run_eval.py --split dev --label replay --responses-dir path/to/responses
    python evals/run_eval.py --split hidden --label baseline --agent-cmd "..." --confirm-hidden

The agent command starts in a fresh process per question, gets {"question", "answer_schema"} as one JSON
object on stdin and writes its full response text to stdout. Nothing else from questions.yaml is passed.

This runner reads the hidden entries. Like grader.py it runs outside the agent's sandbox.

Outputs:
    evals/runs/<UTC timestamp>_<split>_<label>.json         summary: counts, rates and usage totals only (committed)
    evals/runs/detail/<UTC timestamp>_<split>_<label>.json  per-question results, responses and traces (gitignored)
Console: per-question lines for dev only. Hidden and all print rates only.

Guard: with --agent-cmd, if every question failed before reaching the model (the agent exited non-zero or printed
nothing, and reported zero tokens or no USAGE line; timeouts excluded, since a killed agent cannot report what it
spent), the runner prints a warning with the failure reasons, saves no summary and no detail file, and exits 3.

Trace: each agent call gets its own temp file in AGENT_TRACE_FILE. The JSON lines the agent appends there are
stored as "trace" in the detail file only (null with --responses-dir), never in the summary or on the console.
Inspect one dev question with  python evals/show_detail.py <detail file> <id>.

Usage: the agent may write one stderr line  USAGE {"input_tokens": n, "output_tokens": n, "tool_calls": n, ...}.
The summary keeps the totals over all questions (failed calls included), never per-question figures.
Agent configuration: if that line also holds "model", "docs", "brief" and "scope" (strings), the summary stores them once as
agent_config, read from the first question only (null when absent, and with --responses-dir). "reviewer_model" and
"pipeline_max_retries" are added when the agent reports them (the pipeline does).

Pipeline: each agent call also gets a temp file in PIPELINE_RESULT_FILE (other agents ignore it). When the agent is
agent/pipeline.py, it writes its side file there; the runner keeps it in the detail file only and adds a "pipeline"
block to the summary (aggregates only, every split): the analyst's first attempt graded alone (the paired within-run
baseline), what was shipped (correct / wrong / other), what was withheld (blocked_correct / blocked_wrong, graded on
the withheld answer), fixed_by_retry, broken_by_retry, statuses, attempts and tokens split by analyst and reviewer.
A withheld answer prints a marker instead of an envelope, so the main rates count it as a format_error.
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grader  # noqa: E402

ROOT = grader.ROOT
RUNS = ROOT / "evals" / "runs"
PAYLOAD_FIELDS = ("question", "answer_schema")  # the only entry fields the agent ever sees
TRACE_ENV = "AGENT_TRACE_FILE"
PIPELINE_ENV = "PIPELINE_RESULT_FILE"
PIPELINE_CONFIG_KEYS = ("reviewer_model", "pipeline_max_retries")
SHIPPED = ("approved", "approved_after_retry")
USAGE_KEYS = ("input_tokens", "output_tokens", "tool_calls")
CONFIG_KEYS = ("model", "docs", "brief", "scope")
PER_RUN_ONLY = ("usage", "agent_config", "failed_early")  # kept per question in memory, stored only as one run-level value
USAGE_LINE = re.compile(r"^USAGE (\{.*\})\s*$", re.MULTILINE)
RATE_KEYS = ("questions", "answerable", "correct", "accuracy", "wrong_confident_rate", "false_refusal_rate",
             "incomplete_refusal_rate", "format_error_rate")


class AgentFailed(Exception):
    pass


def payload(entry):
    return json.dumps({k: entry[k] for k in PAYLOAD_FIELDS})


def read_trace(path):
    """The JSON lines of a trace file as a list; a line that is not a JSON object is kept as unparsed."""
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = None
        events.append(event if isinstance(event, dict) else {"event": "unparsed", "raw": line})
    return events


def read_side_file(path):
    """The pipeline's side file as a dict, or None (other agents never write it)."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def call_agent(cmd, entry, timeout):
    """Return (stdout, stderr, trace, side). Raises AgentFailed(reason, stderr, trace, side) on a crash, timeout or
    empty output. side is the pipeline's side file, or None."""
    with tempfile.TemporaryDirectory(prefix="agent_trace_") as tmp:
        trace_path, side_path = Path(tmp) / "trace.jsonl", Path(tmp) / "pipeline.json"
        try:
            proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, start_new_session=True,
                                    env={**os.environ, TRACE_ENV: str(trace_path), PIPELINE_ENV: str(side_path)})
        except OSError as exc:
            raise AgentFailed(f"agent failed: {exc}", "", [], None)
        try:
            out, err = proc.communicate(payload(entry), timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole session: shell and its children
            proc.communicate()
            raise AgentFailed(f"agent failed: timeout after {timeout}s", "", read_trace(trace_path),
                              read_side_file(side_path))
        trace, side = read_trace(trace_path), read_side_file(side_path)
    if proc.returncode != 0:
        raise AgentFailed(f"agent failed: exit code {proc.returncode}", err, trace, side)
    if not out.strip():
        raise AgentFailed("agent failed: empty output", err, trace, side)
    return out, err, trace, side


def parse_usage(stderr):
    """The last USAGE line of the agent's stderr as a dict of the three counts, or None."""
    for raw in reversed(USAGE_LINE.findall(stderr or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and all(type(data.get(k)) is int and data[k] >= 0 for k in USAGE_KEYS):
            return {k: data[k] for k in USAGE_KEYS}
    return None


def parse_agent_config(stderr):
    """model, docs, brief and scope from the last USAGE line of the agent's stderr that holds all three, or None."""
    for raw in reversed(USAGE_LINE.findall(stderr or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k] for k in CONFIG_KEYS):
            config = {k: data[k] for k in CONFIG_KEYS}
            config.update({k: data[k] for k in PIPELINE_CONFIG_KEYS if k in data})
            return config
    return None


def usage_totals(items):
    totals = dict.fromkeys(USAGE_KEYS, 0)
    for item in items:
        for k, v in (item.get("usage") or {}).items():
            totals[k] += v
    return totals


def read_response(directory, entry):
    path = Path(directory) / f"{entry['id']}.md"
    if not path.is_file():
        raise AgentFailed("agent failed: no response file")
    text = path.read_text()
    if not text.strip():
        raise AgentFailed("agent failed: empty output")
    return text, ""


def failed_result(entry, reason):
    result = grader.grade(entry, "")  # the standard result dict, outcome format_error
    result["reasons"] = [reason]
    return result


def run_one(args, entry):
    start = time.monotonic()
    response, stderr, trace, failed, side = None, "", None, None, None
    try:
        if args.responses_dir:
            response, stderr = read_response(args.responses_dir, entry)
        else:
            response, stderr, trace, side = call_agent(args.agent_cmd, entry, args.timeout)
        result = grader.grade(entry, response)
    except AgentFailed as exc:
        failed = exc.args[0]
        result = failed_result(entry, exc.args[0])
        if len(exc.args) > 1:
            stderr = exc.args[1] or ""
        if len(exc.args) > 2:
            trace = exc.args[2]
        if len(exc.args) > 3:
            side = exc.args[3]
    usage = parse_usage(stderr)
    tokens = (usage["input_tokens"] + usage["output_tokens"]) if usage else 0
    return {"result": result, "response": response, "trace": trace, "stderr_tail": (stderr or "")[-2000:],
            "seconds": round(time.monotonic() - start, 2), "usage": usage,
            "agent_config": parse_agent_config(stderr),
            "failed_early": bool(failed) and not failed.startswith("agent failed: timeout") and tokens == 0,
            "pipeline": side, "pipeline_outcomes": pipeline_outcomes(entry, side)}


def pipeline_outcomes(entry, side):
    """Grader outcomes of a pipeline side file: first attempt, and the shipped or the withheld answer."""
    if not side:
        return None
    attempts = side.get("attempts") or []
    first = attempts[0].get("response") if attempts else None
    out = {"status": side.get("status"), "attempts": len(attempts),
           "first": grader.grade(entry, first or "")["outcome"], "shipped": None, "withheld": None}
    if side.get("status") in SHIPPED:
        out["shipped"] = grader.grade(entry, side.get("shipped_response") or "")["outcome"]
    elif side.get("status") == "needs_human_review":
        answer = (side.get("handoff") or {}).get("answer")
        out["withheld"] = grader.grade(entry, answer or "")["outcome"]
    return out


def pipeline_block(items):
    """Aggregates over a pipeline run (None when no item has a side file). Counts only, never ids."""
    if not any(i.get("pipeline") for i in items):
        return None
    n = len(items)
    c = dict.fromkeys(("first_attempt_correct", "shipped", "shipped_correct", "shipped_wrong", "shipped_other",
                       "blocked", "blocked_correct", "blocked_wrong", "fixed_by_retry", "broken_by_retry",
                       "analyst_failed", "pipeline_failure"), 0)
    statuses, attempts = {}, {}
    tokens = {"analyst": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
              "reviewer": {"input_tokens": 0, "output_tokens": 0}}
    for i in items:
        o, side = i.get("pipeline_outcomes"), i.get("pipeline")
        if not o:
            c["pipeline_failure"] += 1
            continue
        statuses[o["status"]] = statuses.get(o["status"], 0) + 1
        attempts[str(o["attempts"])] = attempts.get(str(o["attempts"]), 0) + 1
        for stage, keys in tokens.items():
            for k in keys:
                v = ((side.get("tokens") or {}).get(stage) or {}).get(k)
                keys[k] += v if isinstance(v, int) else 0
        first_ok = o["first"] == "correct"
        c["first_attempt_correct"] += first_ok
        shipped_ok = o["shipped"] == "correct"
        if o["status"] in SHIPPED:
            c["shipped"] += 1
            c["shipped_correct"] += shipped_ok
            c["shipped_wrong"] += o["shipped"] == "wrong_confident"
            c["shipped_other"] += o["shipped"] not in ("correct", "wrong_confident")
        elif o["status"] == "needs_human_review":
            c["blocked"] += 1
            c["blocked_correct"] += o["withheld"] == "correct"
            c["blocked_wrong"] += o["withheld"] != "correct"
        else:
            c["analyst_failed"] += 1
        c["fixed_by_retry"] += (not first_ok) and shipped_ok
        c["broken_by_retry"] += first_ok and not shipped_ok
    rated = {k: {"n": v, "of": n, "rate": round(v / n, 4) if n else None} for k, v in c.items()}
    return {**rated, "statuses": dict(sorted(statuses.items())), "attempts": dict(sorted(attempts.items())),
            "tokens": tokens}


def last_message(stderr):
    """The last stderr line that is not a USAGE line (the agent's own error message), or ''."""
    lines = [ln for ln in (stderr or "").splitlines() if ln.strip() and not USAGE_LINE.match(ln)]
    return lines[-1].strip()[:300] if lines else ""


def git_info():
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                             check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                                    check=True).stdout.strip())
        return sha, dirty
    except (OSError, subprocess.CalledProcessError):
        return None, None


def rates_only(summary):
    """summarize() output without per-question lists (failed, manual_review)."""
    return {split: {k: v for k, v in s.items() if k in RATE_KEYS} for split, s in summary.items()}


def earlier_hidden_runs(runs_dir):
    n = 0
    for p in Path(runs_dir).glob("*.json"):
        try:
            n += json.loads(p.read_text()).get("split") in ("hidden", "all")
        except (OSError, json.JSONDecodeError):
            pass
    return n


def run_name(runs_dir, split, label):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "run"
    name, i = f"{stamp}_{split}_{slug}", 2
    while (Path(runs_dir) / f"{name}.json").exists():
        name, i = f"{stamp}_{split}_{slug}-{i}", i + 1
    return stamp, name


def print_rates(summary):
    for split, s in summary.items():
        print(f"[{split}] questions {s['questions']}  correct {s['correct']}  accuracy {s['accuracy']}  "
              f"wrong_confident {s['wrong_confident_rate']}  false_refusal {s['false_refusal_rate']}  "
              f"incomplete_refusal {s['incomplete_refusal_rate']}  format_error {s['format_error_rate']}")


def select(questions, args, parser):
    entries = [e for e in questions.values() if args.split == "all" or e["split"] == args.split]
    if args.questions:
        if args.split != "dev":
            parser.error("--questions works with --split dev only")
        ids = [q.strip() for q in args.questions.split(",") if q.strip()]
        bad = [q for q in ids if q not in questions or questions[q]["split"] != "dev"]
        if bad:
            parser.error(f"not dev questions: {', '.join(bad)}")
        entries = [questions[q] for q in ids]
    return entries


def main(argv=None):
    p = argparse.ArgumentParser(description="Run and grade the analyst agent on a split.")
    p.add_argument("--split", required=True, choices=["dev", "hidden", "all"])
    p.add_argument("--label", required=True, help="free text, e.g. baseline")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--agent-cmd", help="shell command: question JSON on stdin, response text on stdout")
    src.add_argument("--responses-dir", type=Path, help="grade existing DIR/<id>.md files instead")
    p.add_argument("--timeout", type=float, default=300, help="seconds per question (default 300)")
    p.add_argument("--questions", help="comma-separated subset, dev only")
    p.add_argument("--confirm-hidden", action="store_true", help="required for --split hidden or all")
    p.add_argument("--runs-dir", type=Path, default=RUNS, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.split in ("hidden", "all") and not args.confirm_hidden:
        print(f"Refusing --split {args.split} without --confirm-hidden. Hidden runs count: "
              "run them rarely and never tune against them.", file=sys.stderr)
        return 2

    questions = grader.load_questions()
    entries = select(questions, args, p)
    sha, dirty = git_info()  # before writing anything
    runs_dir, show_ids = Path(args.runs_dir), args.split == "dev"

    items = []
    for entry in entries:
        item = run_one(args, entry)
        items.append(item)
        if show_ids:
            r = item["result"]
            print(f"{r['id']}  {r['outcome']:<18}  {'; '.join(r['reasons'])}".rstrip())
    if args.agent_cmd and items and all(i["failed_early"] for i in items):
        reasons = sorted({i["result"]["reasons"][0] for i in items})
        print("WARNING: every question failed before reaching the model; summary not saved", file=sys.stderr)
        print(f"failures: {'; '.join(reasons)}", file=sys.stderr)
        message = last_message(items[0]["stderr_tail"])
        if message:
            print(f"agent stderr: {message}", file=sys.stderr)
        return 3
    results = [i["result"] for i in items]
    full = grader.summarize(results)

    stamp, name = run_name(runs_dir, args.split, args.label)
    summary = {"timestamp": stamp, "split": args.split, "label": args.label, "git_sha": sha, "git_dirty": dirty,
               "agent_cmd": args.agent_cmd,
               "responses_dir": str(args.responses_dir) if args.responses_dir else None,
               "timeout": args.timeout, "n_questions": len(entries), "rates": rates_only(full),
               "usage": None if args.responses_dir else usage_totals(items),
               "agent_config": items[0]["agent_config"] if items else None}
    pipe = pipeline_block(items)
    if pipe is not None:
        summary["pipeline"] = pipe
    if args.split != "dev":
        summary["earlier_hidden_runs"] = earlier_hidden_runs(runs_dir)
    detail = {**summary, "summary": full,
              "questions": [{"id": i["result"]["id"], **{k: v for k, v in i.items() if k not in PER_RUN_ONLY}}
                            for i in items]}

    (runs_dir / "detail").mkdir(parents=True, exist_ok=True)
    summary_path, detail_path = runs_dir / f"{name}.json", runs_dir / "detail" / f"{name}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    detail_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False) + "\n")

    if show_ids:
        print()
    print_rates(summary["rates"])
    if summary["usage"] is not None:
        u = summary["usage"]
        print(f"usage: input_tokens {u['input_tokens']}  output_tokens {u['output_tokens']}  "
              f"tool_calls {u['tool_calls']}")
    if summary["agent_config"] is not None:
        c = summary["agent_config"]
        line = f"agent_config: model {c['model']}  docs {c['docs']}  brief {c['brief']}  scope {c['scope']}"
        if "reviewer_model" in c or "pipeline_max_retries" in c:
            line += f"  reviewer_model {c.get('reviewer_model')}  pipeline_max_retries {c.get('pipeline_max_retries')}"
        print(line)
    if "pipeline" in summary:
        pb = summary["pipeline"]
        print("pipeline: " + "  ".join(f"{k} {pb[k]['n']}/{pb[k]['of']}" for k in (
            "first_attempt_correct", "shipped_correct", "shipped_wrong", "blocked_correct", "blocked_wrong",
            "fixed_by_retry", "broken_by_retry")))
        t = pb["tokens"]
        print(f"pipeline tokens: analyst {t['analyst']['input_tokens']}+{t['analyst']['output_tokens']}  "
              f"reviewer {t['reviewer']['input_tokens']}+{t['reviewer']['output_tokens']}")
    if show_ids:
        manual = full["all"]["manual_review"]
        print(f"manual_review: {', '.join(manual) if manual else 'none'}")
    else:
        print(f"earlier hidden runs: {summary['earlier_hidden_runs']}")
    print(f"summary: {summary_path}\ndetail:  {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
