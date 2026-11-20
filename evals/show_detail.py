"""Show one dev question from an eval run's detail file: question, agent trace, raw response and graded outcome.

Usage (from the repo root):
    python evals/show_detail.py evals/runs/detail/<run>.json q001

Dev only: a question that is not in the dev split is refused with exit code 2, before the detail file is read.
Exit codes: 0 shown, 2 refused or not found.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grader  # noqa: E402


def fail(msg):
    print(f"show_detail: {msg}", file=sys.stderr)
    return 2


def indent(text, prefix="    "):
    return "\n".join(prefix + line for line in str(text).splitlines()) or prefix


def format_event(e):
    kind = e.get("event")
    if kind == "text":
        return f"[text, turn {e.get('turn')}]\n{indent(e.get('text', ''))}"
    if kind == "sql":
        return f"[sql, turn {e.get('turn')}, {e.get('tool_use_id')}]\n{indent(e.get('sql', ''))}"
    if kind == "tool_result":
        label = "result ERROR" if e.get("is_error") else "result"
        return f"[{label}, {e.get('tool_use_id')}]\n{indent(e.get('content', ''))}"
    if kind == "budget_message":
        return f"[budget]\n{indent(e.get('text', ''))}"
    if kind == "final":
        return "[final] (full text under == response)"
    if kind == "error":
        return f"[error] {e.get('message', '')}"
    return f"[{kind}]\n{indent(json.dumps(e, ensure_ascii=False))}"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("detail", type=Path, help="a detail file from evals/runs/detail/")
    p.add_argument("question", help="dev question id, e.g. q001")
    args = p.parse_args(argv)

    questions = grader.load_questions()
    entry = questions.get(args.question)
    if entry is None:
        return fail(f"unknown question {args.question}")
    if entry["split"] != "dev":
        return fail(f"{args.question} is not a dev question; this tool is dev only")

    try:
        detail = json.loads(args.detail.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"cannot read detail file: {type(exc).__name__}")
    matches = [q for q in detail.get("questions", []) if q.get("id") == args.question]
    if not matches:
        return fail(f"{args.question} is not in this detail file")
    item = matches[0]
    result = item.get("result") or {}
    if result.get("split") != "dev":  # second guard, in case the detail file disagrees with questions.yaml
        return fail(f"{args.question} is not a dev question in this detail file; this tool is dev only")

    out = [f"== question {args.question}", entry["question"].strip(), "", "== trace"]
    trace = item.get("trace")
    out += [format_event(e) for e in trace] if trace else ["(no trace)"]
    out += ["", "== response", item.get("response") if item.get("response") is not None else "(no response)", "",
            "== outcome",
            f"outcome: {result.get('outcome')}",
            f"status: {result.get('status')} (expected {result.get('expected_status')})"]
    out += ["reasons:"] + [f"  - {r}" for r in result.get("reasons") or ["(none)"]]
    for key in ("notes", "manual_review"):
        if result.get(key):
            out += [f"{key}:"] + [f"  - {x}" for x in result[key]]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
