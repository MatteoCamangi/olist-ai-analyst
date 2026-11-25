"""Build the reviewer's held-out test set from the analyst's saved HIDDEN answers.

Usage (from the repo root, with the venv active):
    python evals/reviewer/heldout/build_heldout.py

Reads the analyst eval runs whose committed summary (evals/runs/*.json) has split "hidden" and a label in
SOURCE_LABELS, and their detail files (evals/runs/detail/<same name>, gitignored). A run whose detail file is missing
is skipped and named by its label only.

Writes two gitignored files next to this script:
    cases.heldout.json   reviewer-facing fields only: opaque id, question, answer_schema, response, trace
    key.heldout.json     opaque id -> source label, source run, question id, grader outcome, manual-review flag,
                         group, expected verdict. Read only by the runner, for scoring.

Labels: grader outcome wrong_confident or false_refusal -> expected verdict "reject". Outcome correct -> no expected
verdict (unlabelled): a correct headline can still carry unsupported side claims. format_error and incomplete_refusal
answers are left out (no real answer to review), as are answers without a response or a query.

Ids h001, h002, ... are assigned after a shuffle with a fixed seed, so their order says nothing about the source.
The terminal output is aggregates only: no ids, question ids, questions, responses or values.
"""
import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(ROOT / "evals"))
import grader  # noqa: E402

RUNS = ROOT / "evals" / "runs"
SOURCE_LABELS = ("v3-harder", "abl-floor", "grid-marts-bare", "grid-raw-bare")
SEED = 20261125
CASES_FILE = "cases.heldout.json"
KEY_FILE = "key.heldout.json"
REVIEWER_FIELDS = ("question", "answer_schema", "response", "trace")  # what the dev cases give the reviewer
EXPECTED = {"wrong_confident": "reject", "false_refusal": "reject", "correct": None}
EXCLUDED = ("format_error", "incomplete_refusal")


def find_runs(runs_dir, labels=SOURCE_LABELS, log=print):
    """[(label, run name, detail path)] for hidden summaries with a source label; missing details are skipped."""
    found = []
    for path in sorted(Path(runs_dir).glob("*.json")):
        try:
            summary = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if summary.get("split") != "hidden" or summary.get("label") not in labels:
            continue
        detail = Path(runs_dir) / "detail" / path.name
        if not detail.is_file():
            log(f"skipped run {summary['label']}: detail file missing")
            continue
        found.append((summary["label"], path.stem, detail))
    for label in labels:
        if not any(f[0] == label for f in found):
            log(f"no usable run for label {label}")
    return found


def has_query(trace):
    return isinstance(trace, list) and any(isinstance(e, dict) and e.get("event") == "sql" for e in trace)


def build(runs_dir, questions, seed=SEED, log=print):
    """(cases, key, stats). Only hidden questions are accepted; anything else stops the build."""
    hidden = {qid: e for qid, e in questions.items() if e["split"] == "hidden"}
    rows, skipped = [], Counter()
    for label, run, detail_path in find_runs(runs_dir, log=log):
        detail = json.loads(detail_path.read_text())
        if detail.get("split") != "hidden":
            raise SystemExit(f"build_heldout: run {label} is not a hidden run")
        for item in detail.get("questions", []):
            result = item.get("result") or {}
            qid = item.get("id")
            if result.get("split") != "hidden" or qid not in hidden:
                raise SystemExit(f"build_heldout: run {label} holds a non-hidden item")
            outcome = result.get("outcome")
            if outcome in EXCLUDED:
                skipped[outcome] += 1
                continue
            if outcome not in EXPECTED:
                skipped["other_outcome"] += 1
                continue
            if not (item.get("response") or "").strip() or not has_query(item.get("trace")):
                skipped["no_response_or_query"] += 1
                continue
            rows.append({"label": label, "run": run, "qid": qid, "outcome": outcome,
                         "manual_review": bool(result.get("manual_review")),
                         "case": {"question": hidden[qid]["question"], "answer_schema": hidden[qid]["answer_schema"],
                                  "response": item["response"], "trace": item["trace"]}})
    rows.sort(key=lambda r: (r["label"], r["run"], r["qid"]))
    random.Random(seed).shuffle(rows)
    cases, key = [], {}
    for n, r in enumerate(rows, 1):
        hid = f"h{n:03d}"
        cases.append({"id": hid, **{k: r["case"][k] for k in REVIEWER_FIELDS}})
        expected = EXPECTED[r["outcome"]]
        key[hid] = {"source_label": r["label"], "source_run": r["run"], "question_id": r["qid"],
                    "outcome": r["outcome"], "manual_review": r["manual_review"],
                    "group": "expected_reject" if expected else "unlabelled_correct", "expected_verdict": expected}
    stats = {"total": len(cases),
             "per_label": dict(sorted(Counter(r["label"] for r in rows).items())),
             "per_outcome": dict(sorted(Counter(r["outcome"] for r in rows).items())),
             "skipped": dict(sorted(skipped.items())),
             "distinct_questions_expected_reject": len({r["qid"] for r in rows if EXPECTED[r["outcome"]]})}
    return cases, key, stats


def write(cases, key, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CASES_FILE).write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
    (out_dir / KEY_FILE).write_text(json.dumps(key, indent=2) + "\n")


def report(stats, out=print):
    """Aggregates only."""
    out(f"held-out cases: {stats['total']}")
    out("per source label: " + ", ".join(f"{k} {v}" for k, v in stats["per_label"].items()))
    out("per outcome: " + ", ".join(f"{k} {v}" for k, v in stats["per_outcome"].items()))
    out("skipped: " + (", ".join(f"{k} {v}" for k, v in stats["skipped"].items()) or "none"))
    out(f"distinct questions among expected-reject answers: {stats['distinct_questions_expected_reject']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the reviewer's held-out cases (aggregates printed only).")
    p.add_argument("--runs-dir", type=Path, default=RUNS)
    p.add_argument("--questions", type=Path, default=grader.QUESTIONS)
    p.add_argument("--out-dir", type=Path, default=HERE)
    args = p.parse_args(argv)
    cases, key, stats = build(args.runs_dir, grader.load_questions(args.questions))
    write(cases, key, args.out_dir)
    report(stats)
    print(f"written: {args.out_dir / CASES_FILE} and {args.out_dir / KEY_FILE} (gitignored; do not open while "
          "working on the reviewer)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
