"""Reviewer agent: reviews one analyst answer (question, response, trace) and returns a verdict as JSON.

Usage (from the repo root):
    python agent/reviewer.py < case.json
    python agent/reviewer.py path/to/case.json

Input: one JSON object. Only INPUT_FIELDS are read (question, answer_schema, response, trace); any other key is
ignored and never reaches the model. Output on stdout: the verdict object
    {"verdict": "approve|revise|reject", "reason": str,
     "findings": [{"check": str, "severity": "high|medium|low", "evidence": str, "fix": str}]}
or, when the model's reply is not exactly such an object, {"verdict": null, "error": "format_error"} (exit 0, so a
runner can count it; a verdict is never guessed). At exit stderr always ends with one line
    USAGE {"input_tokens": n, "output_tokens": n, "model": s}      (model is null when REVIEWER_MODEL is invalid)

Exactly one model call, no tools. The system prompt is agent/REVIEWER_BRIEF.md, docs/metrics.md and
evals/ANSWER_FORMAT.md, verbatim and in that order, sent as one cached block. The trace is reduced to its queries and
their results, numbered Query 1..n so findings can cite them.

Environment: REVIEWER_MODEL (model id starting with claude-, default claude-sonnet-5-5); OLIST_AGENT_API_KEY.
Streaming, retries (429, 5xx, in-stream overloaded/api errors) and usage counting are the analyst's own functions.
There is no server-side refusal fallback: it would answer with another model and blur which model was measured.

Exit codes: 0 verdict or format_error printed, 1 failure (API error after retries, refusal, output limit, empty
reply), 2 bad input, invalid REVIEWER_MODEL or missing OLIST_AGENT_API_KEY.
"""
import json
import os
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyst  # noqa: E402  (streaming call, retries, usage, client and model defaults)

ROOT = analyst.ROOT
MODEL_ENV = "REVIEWER_MODEL"
MAX_TOKENS = 64000  # streamed; adaptive thinking and the JSON share it
INPUT_FIELDS = ("question", "answer_schema", "response", "trace")
REVIEWER_DOCS = (  # every file the system prompt is built from, in order; nothing else is read for the model
    ("Reviewer brief (agent/REVIEWER_BRIEF.md)", "agent/REVIEWER_BRIEF.md"),
    ("Metric definitions (docs/metrics.md)", "docs/metrics.md"),
    ("Answer format spec (evals/ANSWER_FORMAT.md)", "evals/ANSWER_FORMAT.md"),
)
VERDICTS = ("approve", "revise", "reject")
CHECKS = ("numbers", "definitions", "grain_population", "assumptions", "refusals_false_premises",
          "claims_beyond_data", "format")  # REVIEWER_BRIEF.md section 3, in order
SEVERITIES = ("high", "medium", "low")
FINDING_KEYS = ("check", "severity", "evidence", "fix")
FORMAT_ERROR = {"verdict": None, "error": "format_error"}

OUTPUT_CONTRACT = f"""Reply with one JSON object and nothing else: no prose before or after it, no code fences.
It must have exactly these keys:
{{"verdict": "approve" | "revise" | "reject",
 "reason": "<one line>",
 "findings": [{{"check": "<check>", "severity": "high" | "medium" | "low", "evidence": "<query number or exact text>", "fix": "<guidance>"}}]}}
check is one of: {", ".join(CHECKS)}.
Use an empty findings list when there are none. Cite queries as "query N" using the numbers above."""


class InputError(ValueError):
    pass


class FormatError(ValueError):
    pass


# -- prompt ----------------------------------------------------------------------------------------------

def build_system_prompt():
    return "\n\n".join(f"# {title}\n\n" + (ROOT / path).read_text().strip() for title, path in REVIEWER_DOCS)


def queries(trace):
    """[(n, sql, result, is_error)] from the trace's sql and tool_result events; tool_use ids are not kept."""
    results = {e.get("tool_use_id"): e for e in trace if e.get("event") == "tool_result"}
    out = []
    for e in trace:
        if e.get("event") == "sql":
            r = results.get(e.get("tool_use_id")) or {}
            out.append((len(out) + 1, e.get("sql", ""), r.get("content", "(no result)"), bool(r.get("is_error"))))
    return out


def build_user_message(case):
    missing = [k for k in INPUT_FIELDS if k not in case]
    if missing:
        raise InputError(f"missing {', '.join(missing)}")
    if not isinstance(case["trace"], list):
        raise InputError("trace must be a list")
    schema = yaml.safe_dump(case["answer_schema"], sort_keys=False, allow_unicode=True).strip()
    parts = [f"Question:\n{case['question'].strip()}", f"answer_schema:\n```yaml\n{schema}\n```", "Trace:"]
    qs = queries(case["trace"])
    for n, sql, result, is_error in qs:
        label = "Result (error)" if is_error else "Result"
        parts.append(f"Query {n}:\n```sql\n{sql.strip()}\n```\n{label} {n}:\n```\n{result.strip()}\n```")
    if not qs:
        parts.append("(no queries)")
    parts += [f"Analyst response:\n<<<\n{case['response'].strip()}\n>>>", OUTPUT_CONTRACT]
    return "\n\n".join(parts)


def build_request(case, model):
    return {"model": model, "max_tokens": MAX_TOKENS,
            "system": [{"type": "text", "text": build_system_prompt(), "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": build_user_message(case)}]}


# -- output ----------------------------------------------------------------------------------------------

def parse_verdict(text):
    """The verdict object if text is exactly one valid JSON object; raises FormatError naming the rule."""
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError):
        raise FormatError("reply is not exactly one JSON object") from None
    if not isinstance(data, dict):
        raise FormatError("reply is not a JSON object")
    if set(data) != {"verdict", "reason", "findings"}:
        raise FormatError("keys must be exactly verdict, reason, findings")
    if data["verdict"] not in VERDICTS:
        raise FormatError("verdict is not approve, revise or reject")
    if not isinstance(data["reason"], str) or not data["reason"].strip():
        raise FormatError("reason must be a non-empty string")
    if not isinstance(data["findings"], list):
        raise FormatError("findings must be a list")
    for f in data["findings"]:
        if not isinstance(f, dict) or set(f) != set(FINDING_KEYS):
            raise FormatError("each finding needs exactly check, severity, evidence, fix")
        if not all(isinstance(f[k], str) for k in FINDING_KEYS):
            raise FormatError("finding fields must be strings")
        if f["check"] not in CHECKS:
            raise FormatError("unknown check name")
        if f["severity"] not in SEVERITIES:
            raise FormatError("severity is not high, medium or low")
    return {"verdict": data["verdict"], "reason": data["reason"],
            "findings": [{k: f[k] for k in FINDING_KEYS} for f in data["findings"]]}


def review(case, client, usage, model, sleep=time.sleep):
    """Exactly one model call. Returns the reply text; updates usage in place even when this raises."""
    response = analyst.call_with_retry(client, sleep, **build_request(case, model))
    analyst._add_usage(usage, response.usage)
    if response.stop_reason == "refusal":
        raise analyst.AgentError("the model refused (stop_reason refusal)")
    if response.stop_reason == "max_tokens":
        raise analyst.AgentError(f"output limit hit: {MAX_TOKENS} max_tokens (stop_reason max_tokens)")
    if response.stop_reason != "end_turn":
        raise analyst.AgentError(f"unexpected stop_reason {response.stop_reason}")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if not text:
        raise analyst.AgentError("the model returned no text")
    return text


# -- CLI -------------------------------------------------------------------------------------------------

def read_case(argv, stdin):
    if len(argv) > 1:
        raise InputError("give at most one path")
    raw = Path(argv[0]).read_text() if argv else stdin.read()
    case = json.loads(raw)
    if not isinstance(case, dict):
        raise InputError("input must be a JSON object")
    build_user_message(case)  # validates the four fields
    return {k: case[k] for k in INPUT_FIELDS}


def main(argv=None, stdin=None, stdout=None, stderr=None, env=None, client_factory=None, sleep=time.sleep):
    argv = sys.argv[1:] if argv is None else argv
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    env = os.environ if env is None else env
    usage, model = {"input_tokens": 0, "output_tokens": 0}, None
    try:
        model = env.get(MODEL_ENV) or analyst.MODEL
        if not analyst._MODEL_ID.fullmatch(model):
            print(f"reviewer: {MODEL_ENV} must be a model id starting with claude-", file=stderr)
            model = None
            return 2
        try:
            case = read_case(argv, stdin)
        except (OSError, ValueError) as exc:  # JSONDecodeError and InputError are ValueErrors
            print(f"reviewer: bad input: {type(exc).__name__}: {exc}"[:300], file=stderr)
            return 2
        api_key = env.get(analyst.KEY_ENV)
        if not api_key:
            print(f"reviewer: {analyst.KEY_ENV} is not set", file=stderr)
            return 2
        try:
            client = (client_factory or analyst._default_client)(api_key)
            text = review(case, client, usage, model, sleep=sleep)
        except analyst.AgentError as exc:
            print(f"reviewer: {exc}", file=stderr)
            return 1
        except Exception as exc:  # never echo request data
            print(f"reviewer: failed: {type(exc).__name__}", file=stderr)
            return 1
        try:
            result = parse_verdict(text)
        except FormatError as exc:
            print(f"reviewer: format_error: {exc}", file=stderr)
            result = FORMAT_ERROR
        print(json.dumps(result, ensure_ascii=False), file=stdout)
        return 0
    finally:
        print("USAGE " + json.dumps({**usage, "model": model}), file=stderr)
        stderr.flush()


if __name__ == "__main__":
    sys.exit(main())
