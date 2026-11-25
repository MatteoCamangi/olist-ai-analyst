"""Tests for the reviewer held-out tooling. Synthetic fixtures only: fake questions, fake analyst runs and fake agents
in temp dirs. No real hidden detail file is ever opened, and no API call is made.

Run from the repo root:  python evals/reviewer/heldout/test_heldout.py   (or pytest evals/reviewer/heldout/test_heldout.py)
"""
import io
import json
import re
import subprocess
import sys
import tempfile
import traceback
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
sys.path.insert(0, str(HERE))
import build_heldout as bh  # noqa: E402

QMARK, RMARK = "QTEXT-MARKER-5c1e", "RESPONSE-MARKER-9a7d"
FAKE_IDS = ("q901", "q902", "q903", "q904")
KEY_WORDS = ("expected", "outcome", "source_label", "source_run", "question_id", "manual_review", "group",
             "wrong_confident", "false_refusal")


def sql_trace(n=1):
    return [{"event": "sql", "turn": 1, "tool_use_id": "t1", "sql": f"SELECT {n} AS n FROM fct_orders"},
            {"event": "tool_result", "tool_use_id": "t1", "content": f"n\n{n}\n(1 row)", "is_error": False},
            {"event": "final", "text": "x"}]


def write_questions(path):
    entries = [{"id": q, "split": "hidden", "type": "numeric", "question": f"{QMARK} question {q}?",
                "answer_schema": {"status": "answered | cannot_answer", "answer": {"n": "integer"}},
                "expected": {"n": 1}, "tolerance": 0, "gold_sql": f"evals/gold/hidden/{q}.sql"} for q in FAKE_IDS]
    entries.append({"id": "q900", "split": "dev", "type": "numeric", "question": "dev question?",
                    "answer_schema": {}, "expected": {"n": 1}, "tolerance": 0, "gold_sql": "x"})
    import yaml
    path.write_text(yaml.safe_dump(entries))


def item(qid, outcome, response=True, trace=True, manual=False):
    return {"id": qid, "result": {"id": qid, "split": "hidden", "outcome": outcome,
                                  "manual_review": [{"rule": "r", "snippet": "secret snippet"}] if manual else []},
            "response": f"{RMARK} answer {len(qid)}" if response else None,
            "trace": sql_trace() if trace else [], "stderr_tail": "", "seconds": 1.0}


RUNS = {  # label -> items
    "v3-harder": [item("q901", "correct"), item("q902", "wrong_confident", manual=True), item("q903", "format_error"),
                  item("q904", "incomplete_refusal")],
    "abl-floor": [item("q901", "wrong_confident"), item("q902", "wrong_confident"), item("q903", "false_refusal"),
                  item("q904", "correct", trace=False)],
    "grid-marts-bare": [item("q901", "correct"), item("q902", "correct", response=False), item("q903", "correct")],
}


def make_fixture(tmp, runs=RUNS, missing=("grid-raw-bare",)):
    tmp = Path(tmp)
    runs_dir = tmp / "runs"
    (runs_dir / "detail").mkdir(parents=True)
    write_questions(tmp / "questions.yaml")
    for n, (label, items) in enumerate(runs.items()):
        name = f"2026112{n}T000000Z_hidden_{label}"
        (runs_dir / f"{name}.json").write_text(json.dumps({"split": "hidden", "label": label, "rates": {}}))
        (runs_dir / "detail" / f"{name}.json").write_text(json.dumps({"split": "hidden", "label": label,
                                                                       "questions": items}))
    for label in missing:  # a summary whose detail file is missing
        (runs_dir / f"20261129T000000Z_hidden_{label}.json").write_text(json.dumps({"split": "hidden", "label": label}))
    (runs_dir / "20261128T000000Z_dev_v3-harder.json").write_text(json.dumps({"split": "dev", "label": "v3-harder"}))
    return runs_dir, tmp / "questions.yaml"


def build_fixture(tmp, **kw):
    runs_dir, questions = make_fixture(tmp, **kw)
    out = io.StringIO()
    with redirect_stdout(out):
        cases, key, stats = bh.build(runs_dir, bh.grader.load_questions(questions))
    return cases, key, stats, out.getvalue()


# -- builder ---------------------------------------------------------------------------------------------

def test_outcomes_map_to_expected_verdicts():
    with tempfile.TemporaryDirectory() as tmp:
        cases, key, stats, _ = build_fixture(tmp)
    by = {(k["source_label"], k["question_id"]): k for k in key.values()}
    assert by[("v3-harder", "q901")]["expected_verdict"] is None and by[("v3-harder", "q901")]["group"] == "unlabelled_correct"
    assert by[("v3-harder", "q902")]["expected_verdict"] == "reject" and by[("v3-harder", "q902")]["group"] == "expected_reject"
    assert by[("abl-floor", "q903")]["outcome"] == "false_refusal" and by[("abl-floor", "q903")]["expected_verdict"] == "reject"
    assert by[("v3-harder", "q902")]["manual_review"] is True and by[("abl-floor", "q901")]["manual_review"] is False
    assert "secret snippet" not in json.dumps(key), "manual-review snippets must not be copied"
    assert stats["per_outcome"] == {"correct": 3, "false_refusal": 1, "wrong_confident": 3}
    assert stats["distinct_questions_expected_reject"] == 3  # q901, q902, q903


def test_excluded_answers():
    with tempfile.TemporaryDirectory() as tmp:
        cases, key, stats, _ = build_fixture(tmp)
    outcomes = {k["outcome"] for k in key.values()}
    assert not outcomes & {"format_error", "incomplete_refusal"}
    assert stats["skipped"] == {"format_error": 1, "incomplete_refusal": 1, "no_response_or_query": 2}
    assert stats["total"] == len(cases) == len(key) == 7
    assert stats["per_label"] == {"abl-floor": 3, "grid-marts-bare": 2, "v3-harder": 2}


def test_ids_opaque_and_shuffled_deterministically():
    with tempfile.TemporaryDirectory() as tmp:
        cases, key, _, _ = build_fixture(tmp)
    with tempfile.TemporaryDirectory() as tmp:
        cases2, key2, _, _ = build_fixture(tmp)
    assert [c["id"] for c in cases] == [f"h{n:03d}" for n in range(1, len(cases) + 1)]
    assert key == key2 and cases == cases2, "same seed, same order"
    order = [(key[c["id"]]["source_label"], key[c["id"]]["question_id"]) for c in cases]
    assert order != sorted(order), "ids must not follow the source order"
    runs_dir_items = [r for r in order]
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir, questions = make_fixture(tmp)
        with redirect_stdout(io.StringIO()):
            other, other_key, _ = bh.build(runs_dir, bh.grader.load_questions(questions), seed=1)
    assert [(other_key[c["id"]]["source_label"], other_key[c["id"]]["question_id"]) for c in other] != runs_dir_items
    for c in cases:
        assert re.fullmatch(r"h\d{3}", c["id"]) and not re.search(r"q\d{3}", c["id"])


def test_reviewer_file_has_no_key_fields():
    with tempfile.TemporaryDirectory() as tmp:
        cases, key, _, _ = build_fixture(tmp)
        bh.write(cases, key, Path(tmp) / "out")
        written = json.loads((Path(tmp) / "out" / bh.CASES_FILE).read_text())
        text = (Path(tmp) / "out" / bh.CASES_FILE).read_text()
        written_key = json.loads((Path(tmp) / "out" / bh.KEY_FILE).read_text())
    assert written == cases and written_key == key
    for c in written:
        assert set(c) == {"id", "question", "answer_schema", "response", "trace"}, set(c)
    for word in KEY_WORDS:
        assert word not in text, word
    for c in written:  # a question id may only appear inside the question text itself (true of these fakes only)
        assert not re.search(r"\bq9\d\d\b", json.dumps({k: v for k, v in c.items() if k != "question"}))
    assert set(written_key) == {c["id"] for c in written}


def test_printed_output_is_aggregates_only():
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir, questions = make_fixture(tmp)
        proc = subprocess.run([sys.executable, str(HERE / "build_heldout.py"), "--runs-dir", str(runs_dir),
                               "--questions", str(questions), "--out-dir", str(Path(tmp) / "out")],
                              capture_output=True, text=True, timeout=60)
        written = json.loads((Path(tmp) / "out" / bh.KEY_FILE).read_text())
    assert proc.returncode == 0, proc.stderr[-500:]
    out = proc.stdout + proc.stderr
    for forbidden in (QMARK, RMARK, "secret snippet", "SELECT", *FAKE_IDS, *written):
        assert forbidden not in out, forbidden
    assert not re.search(r"\bh\d{3}\b", out) and not re.search(r"\bq\d{3}\b", out)
    assert "held-out cases: 7" in out and "distinct questions among expected-reject answers: 3" in out
    assert "skipped run grid-raw-bare: detail file missing" in out


def test_only_hidden_items_accepted():
    bad = {"v3-harder": [dict(item("q901", "correct"), result={"split": "dev", "outcome": "correct"})]}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            build_fixture(tmp, runs=bad, missing=())
        except SystemExit as exc:
            assert "non-hidden" in str(exc) and "q901" not in str(exc)
        else:
            raise AssertionError("a dev item was accepted")


# ---------------------------------------------------------------------------------------------------------

def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            print(f"       at line {tb.lineno}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
