"""Run the analyst agent on every question of a split, grade the responses and save the run.

Usage (from the repo root):
    python evals/run_eval.py --split dev --label baseline --agent-cmd "python agent/main.py"
    python evals/run_eval.py --split dev --label replay --responses-dir path/to/responses
    python evals/run_eval.py --split hidden --label baseline --agent-cmd "..." --confirm-hidden

The agent command starts in a fresh process per question, gets {"question", "answer_schema"} as one JSON
object on stdin and writes its full response text to stdout. Nothing else from questions.yaml is passed.

This runner reads the hidden entries. Like grader.py it runs outside the agent's sandbox.

Outputs:
    evals/runs/<UTC timestamp>_<split>_<label>.json         summary: counts and rates only (committed)
    evals/runs/detail/<UTC timestamp>_<split>_<label>.json  per-question results and responses (gitignored)
Console: per-question lines for dev only. Hidden and all print rates only.
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grader  # noqa: E402

ROOT = grader.ROOT
RUNS = ROOT / "evals" / "runs"
PAYLOAD_FIELDS = ("question", "answer_schema")  # the only entry fields the agent ever sees
RATE_KEYS = ("questions", "answerable", "correct", "accuracy", "wrong_confident_rate", "false_refusal_rate",
             "incomplete_refusal_rate", "format_error_rate")


class AgentFailed(Exception):
    pass


def payload(entry):
    return json.dumps({k: entry[k] for k in PAYLOAD_FIELDS})


def call_agent(cmd, entry, timeout):
    """Return (stdout, stderr). Raises AgentFailed on a crash, timeout or empty output."""
    try:
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True)
    except OSError as exc:
        raise AgentFailed(f"agent failed: {exc}")
    try:
        out, err = proc.communicate(payload(entry), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # the whole session: shell and its children
        proc.communicate()
        raise AgentFailed(f"agent failed: timeout after {timeout}s")
    if proc.returncode != 0:
        raise AgentFailed(f"agent failed: exit code {proc.returncode}", err)
    if not out.strip():
        raise AgentFailed("agent failed: empty output", err)
    return out, err


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
    response, stderr = None, ""
    try:
        if args.responses_dir:
            response, stderr = read_response(args.responses_dir, entry)
        else:
            response, stderr = call_agent(args.agent_cmd, entry, args.timeout)
        result = grader.grade(entry, response)
    except AgentFailed as exc:
        result = failed_result(entry, exc.args[0])
        if len(exc.args) > 1:
            stderr = exc.args[1] or ""
    return {"result": result, "response": response, "stderr_tail": (stderr or "")[-2000:],
            "seconds": round(time.monotonic() - start, 2)}


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
    results = [i["result"] for i in items]
    full = grader.summarize(results)

    stamp, name = run_name(runs_dir, args.split, args.label)
    summary = {"timestamp": stamp, "split": args.split, "label": args.label, "git_sha": sha, "git_dirty": dirty,
               "agent_cmd": args.agent_cmd,
               "responses_dir": str(args.responses_dir) if args.responses_dir else None,
               "timeout": args.timeout, "n_questions": len(entries), "rates": rates_only(full)}
    if args.split != "dev":
        summary["earlier_hidden_runs"] = earlier_hidden_runs(runs_dir)
    detail = {**summary, "summary": full,
              "questions": [{"id": i["result"]["id"], **i} for i in items]}

    (runs_dir / "detail").mkdir(parents=True, exist_ok=True)
    summary_path, detail_path = runs_dir / f"{name}.json", runs_dir / "detail" / f"{name}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    detail_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False) + "\n")

    if show_ids:
        print()
    print_rates(summary["rates"])
    if show_ids:
        manual = full["all"]["manual_review"]
        print(f"manual_review: {', '.join(manual) if manual else 'none'}")
    else:
        print(f"earlier hidden runs: {summary['earlier_hidden_runs']}")
    print(f"summary: {summary_path}\ndetail:  {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
