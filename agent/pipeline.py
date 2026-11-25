"""Analyst -> reviewer -> retry pipeline: answers one question, ships it only when the reviewer approves it.

Usage (from the repo root): the same interface as agent/analyst.py.
    echo '{"question": "...", "answer_schema": {...}}' | python agent/pipeline.py

Flow:
1. Run the analyst (agent/analyst.py) on the question, with a private trace file.
2. Give the reviewer (agent/reviewer.py) only {question, answer_schema, response, trace}, where the trace holds just
   the analyst's queries and their results (the reviewer numbers them; no ids or labels reach it).
3. approve -> ship the analyst's answer (status approved). revise or reject -> retry, up to PIPELINE_MAX_RETRIES
   times (default 1): rerun the analyst on the same question with a feedback block (the reviewer's findings and how
   to treat them) appended to its user message (ANALYST_FEEDBACK_FILE), then review the new answer.
   Approved on a retry -> status approved_after_retry. Otherwise -> needs_human_review.
4. Fail closed: a reviewer format error or failure, or a failed retry, ends in needs_human_review.

stdout: the approved analyst response, verbatim (an approved cannot_answer passes through unchanged); or, when the
answer is withheld, one JSON object {"status": "needs_human_review", "withheld": true, "stakeholder_message": ...}.
The stakeholder message comes from a fixed template with no model call and never contains a number or any text of
the withheld answer. The handoff (withheld answer, queries and results, findings with evidence) goes only to the side
file.

Side file: PIPELINE_RESULT_FILE (default pipeline_results/<UTC stamp>_<pid>.json, gitignored) with the config, every
attempt (response, analyst exit code, review, tokens per stage), the status, the shipped response or the handoff,
the stakeholder message and token totals per stage.
Trace: if AGENT_TRACE_FILE is set, every attempt's analyst events (with an "attempt" field) and one "review" event
per review are appended to it.
stderr ends with one line  USAGE {"input_tokens", "output_tokens", "tool_calls", "model", "docs", "brief", "scope",
"reviewer_model", "pipeline_max_retries", "attempts", "status", "analyst_input_tokens", "analyst_output_tokens",
"reviewer_input_tokens", "reviewer_output_tokens"}: totals plus the split by stage.

Environment passes through unchanged to both agents (ANALYST_MODEL, ANALYST_DOCS, ANALYST_BRIEF, ANALYST_SCOPE,
REVIEWER_MODEL, OLIST_AGENT_API_KEY). PIPELINE_ANALYST_CMD and PIPELINE_REVIEWER_CMD replace the two commands (tests).
Exit codes: 0 an answer or a withheld marker was printed, 1 the first analyst run failed, 2 bad input or config.
"""
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RETRIES_ENV = "PIPELINE_MAX_RETRIES"
RESULT_ENV = "PIPELINE_RESULT_FILE"
TRACE_ENV = "AGENT_TRACE_FILE"
FEEDBACK_ENV = "ANALYST_FEEDBACK_FILE"
ANALYST_CMD_ENV, REVIEWER_CMD_ENV = "PIPELINE_ANALYST_CMD", "PIPELINE_REVIEWER_CMD"
DEFAULT_RETRIES = 1
DEFAULT_RESULTS = ROOT / "pipeline_results"
REVIEW_FIELDS = ("question", "answer_schema", "response", "trace")
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

FEEDBACK_TEMPLATE = """Reviewer feedback on your previous answer to this question. A reviewer checked that answer against \
its queries and the metric definitions and raised the findings below. Query numbers refer to the queries of your \
previous attempt, which you cannot see.

{findings}

How to use this feedback:
- Verify every finding with your own queries before changing anything.
- Do not use any number you did not compute yourself in this conversation.
- If a finding is wrong, say so and keep your answer.
Then answer the question again in the required format."""

STAKEHOLDER_TEMPLATE = ("We could not produce a verified answer to this question. The analysis was run twice and "
                        "the automated review could not confirm it{reason}, so no figure is being shared. A human "
                        "analyst will follow up with the full working.")
REASONS = {
    "definitions": "the definition or population could not be confirmed against the metric documentation",
    "grain_population": "the definition or population could not be confirmed against the metric documentation",
    "numbers": "the figures could not be traced to the queries",
    "claims_beyond_data": "some statements could not be supported by the data",
    "assumptions": "some statements could not be supported by the data",
    "format": "some statements could not be supported by the data",
    "refusals_false_premises": "some statements could not be supported by the data",
}


# -- small helpers ---------------------------------------------------------------------------------------

def usage_from(stderr):
    """The last USAGE line of a child's stderr as a dict, or {}."""
    for line in reversed((stderr or "").splitlines()):
        if line.startswith("USAGE "):
            try:
                data = json.loads(line[len("USAGE "):])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return {}


def tokens(usage, *keys):
    return {k: usage.get(k) if isinstance(usage.get(k), int) else 0 for k in keys}


def read_trace(path):
    events = []
    try:
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
    except FileNotFoundError:
        pass
    return events


def review_input(payload, response, events):
    """Exactly the reviewer's four fields; the trace keeps only queries and their results."""
    trace = [e for e in events if e.get("event") in ("sql", "tool_result")]
    return {"question": payload["question"], "answer_schema": payload["answer_schema"], "response": response,
            "trace": trace}


def queries_of(events):
    results = {e.get("tool_use_id"): e for e in events if e.get("event") == "tool_result"}
    return [{"sql": e.get("sql", ""), "result": (results.get(e.get("tool_use_id")) or {}).get("content")}
            for e in events if e.get("event") == "sql"]


def feedback_text(findings):
    lines = []
    for n, f in enumerate(findings, 1):
        lines.append(f"Finding {n}\n- check: {f.get('check')}\n- severity: {f.get('severity')}\n"
                     f"- evidence: {f.get('evidence')}\n- fix: {f.get('fix')}")
    return FEEDBACK_TEMPLATE.format(findings="\n\n".join(lines) or "(no findings were given)")


def stakeholder_message(findings):
    """Fixed template; the reason comes from the most severe finding's check. No numbers, no answer text."""
    ranked = sorted((f for f in findings or [] if isinstance(f, dict)),
                    key=lambda f: SEVERITY_ORDER.get(f.get("severity"), 3))
    reason = REASONS.get(ranked[0].get("check")) if ranked else None
    return STAKEHOLDER_TEMPLATE.format(reason=f": {reason}" if reason else "")


class Pipeline:
    def __init__(self, env, stdout, stderr):
        self.env, self.stdout, self.stderr = env, stdout, stderr
        python = shlex.quote(sys.executable)
        self.analyst_cmd = env.get(ANALYST_CMD_ENV) or f"{python} {shlex.quote(str(ROOT / 'agent' / 'analyst.py'))}"
        self.reviewer_cmd = env.get(REVIEWER_CMD_ENV) or f"{python} {shlex.quote(str(ROOT / 'agent' / 'reviewer.py'))}"
        self.totals = {"analyst": {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
                       "reviewer": {"input_tokens": 0, "output_tokens": 0}}
        self.analyst_config = {}
        self.reviewer_model = None
        self.outer_trace = env.get(TRACE_ENV)

    def child(self, cmd, stdin_text, extra_env):
        env = {k: v for k, v in self.env.items() if k not in (TRACE_ENV, FEEDBACK_ENV, RESULT_ENV)}
        env.update(extra_env)
        proc = subprocess.run(cmd, shell=True, input=stdin_text, capture_output=True, text=True, env=env, cwd=ROOT)
        return proc.returncode, proc.stdout, proc.stderr

    def trace_out(self, event):
        if self.outer_trace:
            with open(self.outer_trace, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def analyst(self, payload, attempt, feedback, tmp):
        trace_path = Path(tmp) / f"trace_{attempt}.jsonl"
        extra = {TRACE_ENV: str(trace_path)}
        if feedback:
            fb = Path(tmp) / f"feedback_{attempt}.txt"
            fb.write_text(feedback)
            extra[FEEDBACK_ENV] = str(fb)
        code, out, err = self.child(self.analyst_cmd, json.dumps(payload), extra)
        usage = usage_from(err)
        t = tokens(usage, "input_tokens", "output_tokens", "tool_calls")
        for k in t:
            self.totals["analyst"][k] += t[k]
        if not self.analyst_config:
            self.analyst_config = {k: usage.get(k) for k in ("model", "docs", "brief", "scope")}
        events = read_trace(trace_path)
        for e in events:
            self.trace_out({**e, "attempt": attempt})
        ok = code == 0 and bool(out.strip())
        return {"response": out if ok else None, "analyst_exit": code, "events": events, "analyst_tokens": t}

    def review(self, payload, response, events, attempt):
        code, out, err = self.child(self.reviewer_cmd, json.dumps(review_input(payload, response, events)), {})
        usage = usage_from(err)
        t = tokens(usage, "input_tokens", "output_tokens")
        for k in t:
            self.totals["reviewer"][k] += t[k]
        if usage.get("model"):
            self.reviewer_model = usage["model"]
        result = {"verdict": None, "error": "agent_failure"}
        if code == 0:
            try:
                data = json.loads(out.strip())
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and data.get("verdict") in ("approve", "revise", "reject") \
                    and isinstance(data.get("findings"), list):
                result = {"verdict": data["verdict"], "reason": data.get("reason"), "findings": data["findings"]}
            else:
                result = {"verdict": None, "error": "format_error"}
        self.trace_out({"event": "review", "attempt": attempt, **result})
        return result, t


def result_path(env):
    if env.get(RESULT_ENV):
        return Path(env[RESULT_ENV])
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_RESULTS / f"{stamp}_{os.getpid()}.json"


def main(stdin=None, stdout=None, stderr=None, env=None):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    env = dict(os.environ if env is None else env)
    try:
        payload = json.loads(stdin.read())
        if not isinstance(payload, dict) or not {"question", "answer_schema"} <= set(payload):
            raise ValueError("need an object with question and answer_schema")
        payload = {"question": payload["question"], "answer_schema": payload["answer_schema"]}
    except ValueError as exc:
        print(f"pipeline: bad input: {exc}", file=stderr)
        return 2
    raw = env.get(RETRIES_ENV, "")
    try:
        max_retries = int(raw) if raw.strip() else DEFAULT_RETRIES
        if max_retries < 0:
            raise ValueError
    except ValueError:
        print(f"pipeline: {RETRIES_ENV} must be a whole number >= 0", file=stderr)
        return 2

    p = Pipeline(env, stdout, stderr)
    attempts, status, shipped, last_findings, last = [], None, None, [], None
    with tempfile.TemporaryDirectory(prefix="pipeline_") as tmp:
        feedback = None
        for attempt in range(1, max_retries + 2):
            run = p.analyst(payload, attempt, feedback, tmp)
            record = {"attempt": attempt, "response": run["response"], "analyst_exit": run["analyst_exit"],
                      "review": None, "tokens": {"analyst": run["analyst_tokens"],
                                                 "reviewer": {"input_tokens": 0, "output_tokens": 0}}}
            attempts.append(record)
            if run["response"] is None:
                if attempt == 1:
                    status = "analyst_failed"
                else:
                    status = "needs_human_review"  # the retry failed: fail closed on the previous answer
                break
            last = run
            review, review_tokens = p.review(payload, run["response"], run["events"], attempt)
            record["review"], record["tokens"]["reviewer"] = review, review_tokens
            if review["verdict"] == "approve":
                status, shipped = ("approved" if attempt == 1 else "approved_after_retry"), run["response"]
                break
            if review["verdict"] is None:  # reviewer format error or failure: nothing to retry on
                status = "needs_human_review"
                break
            last_findings = review["findings"]
            if attempt == max_retries + 1:
                status = "needs_human_review"
                break
            feedback = feedback_text(last_findings)

    message = stakeholder_message(last_findings) if status == "needs_human_review" else None
    handoff = None
    if status == "needs_human_review" and last is not None:
        handoff = {"answer": last["response"], "queries": queries_of(last["events"]), "findings": last_findings}
    config = {**p.analyst_config, "reviewer_model": p.reviewer_model or env.get("REVIEWER_MODEL"),
              "pipeline_max_retries": max_retries}
    side = {"config": config, "status": status, "attempts": attempts, "shipped_response": shipped,
            "withheld": status == "needs_human_review", "handoff": handoff, "stakeholder_message": message,
            "tokens": p.totals}
    path = result_path(env)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(side, indent=2, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"pipeline: cannot write the result file ({type(exc).__name__})", file=stderr)

    code = 0
    if status in ("approved", "approved_after_retry"):
        stdout.write(shipped if shipped.endswith("\n") else shipped + "\n")
    elif status == "needs_human_review":
        print(json.dumps({"status": "needs_human_review", "withheld": True, "stakeholder_message": message}),
              file=stdout)
    else:
        print("pipeline: the analyst failed on the first attempt", file=stderr)
        code = 1
    a, r = p.totals["analyst"], p.totals["reviewer"]
    usage = {"input_tokens": a["input_tokens"] + r["input_tokens"],
             "output_tokens": a["output_tokens"] + r["output_tokens"], "tool_calls": a["tool_calls"],
             **{k: config.get(k) for k in ("model", "docs", "brief", "scope", "reviewer_model",
                                           "pipeline_max_retries")},
             "attempts": len(attempts), "status": status,
             "analyst_input_tokens": a["input_tokens"], "analyst_output_tokens": a["output_tokens"],
             "reviewer_input_tokens": r["input_tokens"], "reviewer_output_tokens": r["output_tokens"]}
    print("USAGE " + json.dumps(usage), file=stderr)
    stderr.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
