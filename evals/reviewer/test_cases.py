"""Tests for evals/reviewer/cases.json, the reviewer agent's dev-only test cases.

Run from the repo root:  python evals/reviewer/test_cases.py   (or pytest evals/reviewer/test_cases.py)
Needs no database. Comparisons with the original runs need the local detail files (gitignored) and say when they
are skipped.
"""
import json
import re
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import grader  # noqa: E402
import make_faults as mf  # noqa: E402

CASES_TEXT = mf.CASES.read_text()
CASES = json.loads(CASES_TEXT)
BY_ID = {c["id"]: c for c in CASES}
QUESTIONS = grader.load_questions()
DEV = {qid for qid, e in QUESTIONS.items() if e["split"] == "dev"}
HIDDEN = {qid: e for qid, e in QUESTIONS.items() if e["split"] != "dev"}
PUBLIC_DOCS = ("docs/metrics.md", "docs/data_notes.md", "evals/ANSWER_FORMAT.md")
FIELDS = {"id": str, "source_question": str, "split": str, "question": str, "answer_schema": dict, "response": str,
          "trace": list, "origin": str, "fault_type": (str, type(None)), "base_case": (str, type(None)),
          "edits": (list, type(None)), "expected_verdict": str, "expected_failed_checks": list, "source": dict,
          "note": str}
SOURCE_FIELDS = {"run", "git_sha", "agent_config", "outcome", "sha256"}
HEADLINE_WRONG = {"wrong_filter", "threshold_off_by_one"}  # the injected edit changes the headline number
SKIPPED = []


# -- helpers ---------------------------------------------------------------------------------------------

def leaves(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from leaves(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from leaves(v)
    else:
        yield obj


def expected_values(entries):
    """Expected strings (4+ chars) and numbers (4+ digits), the grader's leak-check convention."""
    strings, numbers = set(), set()
    for e in entries:
        for v in leaves({k: x for k, x in e["expected"].items()
                         if k not in ("key", "must", "must_not", "allowed", "accepted_interpretations")}):
            if isinstance(v, str) and len(v) >= 4:
                strings.add(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and len(re.sub(r"\D", "", repr(v))) >= 4:
                numbers.add(float(v))
    return strings, numbers


def hidden_only_values():
    """Hidden expected values that are neither dev expected values nor in the public docs."""
    hs, hn = expected_values(HIDDEN.values())
    ds, dn = expected_values(QUESTIONS[q] for q in DEV)
    public = "".join((ROOT / p).read_text() for p in PUBLIC_DOCS)
    return {s for s in hs - ds if s not in public}, hn - dn


def leaked(text, strings, numbers):
    return [s for s in strings if s in text] + \
        [n for n in numbers if repr(n) in text or (n.is_integer() and str(int(n)) in text)]


def detail_items(run):
    path = mf.DETAIL_DIR / f"{run}.json"
    if "_dev_" not in run or not path.is_file():
        return None
    return {q["id"]: q for q in json.loads(path.read_text())["questions"]}


# -- tests -----------------------------------------------------------------------------------------------

def test_required_fields_and_values():
    assert len(CASES) == len(BY_ID) == 17, len(CASES)
    assert [c["id"] for c in CASES] == sorted(BY_ID), "cases are not sorted by id"
    for c in CASES:
        assert set(c) == set(FIELDS), (c["id"], set(c) ^ set(FIELDS))
        for key, kind in FIELDS.items():
            assert isinstance(c[key], kind), (c["id"], key)
        assert set(c["source"]) == SOURCE_FIELDS, c["id"]
        assert c["origin"] in ("natural", "injected") and c["expected_verdict"] in mf.VERDICTS, c["id"]
        assert set(c["expected_failed_checks"]) <= set(mf.CHECKS), c["id"]
        assert (c["expected_verdict"] == "approve") == (not c["expected_failed_checks"]), c["id"]
        injected = c["origin"] == "injected"
        assert injected == (c["fault_type"] is not None) == (c["base_case"] is not None) == bool(c["edits"]), c["id"]
        assert c["response"].strip() and c["note"].strip(), c["id"]
        assert any(e.get("event") == "sql" for e in c["trace"]), c["id"]
    assert sorted(c["fault_type"] for c in CASES if c["fault_type"]) == sorted(mf.FAULT_TYPES)
    natural = [c for c in CASES if c["origin"] == "natural"]
    assert sum(c["expected_verdict"] == "approve" for c in natural) == 10
    assert BY_ID["q010.raw-bare"]["expected_verdict"] == "reject"
    assert BY_ID["q010.raw-bare"]["expected_failed_checks"] == ["definitions"]


def test_verdicts_follow_the_brief():
    brief = (ROOT / "agent" / "REVIEWER_BRIEF.md").read_text()
    assert "Verdict: approve | revise | reject" in brief
    for check in ("Numbers", "Definitions", "Grain and population", "Assumptions", "Refusals and false premises",
                  "Claims beyond the data", "Format"):
        assert f"- {check}:" in brief, check
    assert len(mf.CHECKS) == 7
    for c in CASES:
        if c["origin"] == "injected":
            want = "reject" if c["fault_type"] in HEADLINE_WRONG else "revise"
            assert c["expected_verdict"] == want, (c["id"], c["expected_verdict"])


def test_dev_only_no_hidden_content():
    assert len(HIDDEN) == 12 and len(DEV) == 12
    for c in CASES:
        assert c["split"] == "dev" and c["source_question"] in DEV, c["id"]
        assert c["source_question"] not in mf.EXCLUDED, c["id"]
        assert c["question"] == QUESTIONS[c["source_question"]]["question"], c["id"]
        assert c["answer_schema"] == QUESTIONS[c["source_question"]]["answer_schema"], c["id"]
        assert "_dev_" in c["source"]["run"], c["id"]
    for path in sorted(HERE.glob("*")):
        if not path.is_file():
            continue
        ids = set(re.findall(r"\bq\d{3}\b", path.read_text()))
        assert not ids & set(HIDDEN), f"{path.name}: hidden question ids {sorted(ids & set(HIDDEN))}"
    flat = " ".join(CASES_TEXT.split())
    for qid, e in HIDDEN.items():
        assert " ".join(e["question"].split())[:60] not in flat, f"hidden question text of {qid}"
        assert e["gold_sql"] not in CASES_TEXT, f"hidden gold path of {qid}"
    assert "gold/" not in CASES_TEXT
    strings, numbers = hidden_only_values()
    assert len(strings) > 50 and numbers, "the hidden-value check compares against too little"
    for path in (mf.CASES, HERE / "README.md"):
        hits = leaked(path.read_text(), strings, numbers)
        assert not hits, f"{path.name}: {len(hits)} hidden-only expected value(s)"  # count only, never the values


def test_natural_cases_are_untouched():
    for c in CASES:
        if c["origin"] != "natural":
            continue
        assert mf.content_hash(c["response"], c["trace"]) == c["source"]["sha256"], f"{c['id']} was edited"
        items = detail_items(c["source"]["run"])
        if items is None:
            SKIPPED.append(f"{c['id']}: no local detail file, hash check only")
            continue
        original = items[c["source_question"]]
        assert original["result"]["split"] == "dev"
        assert c["response"] == original["response"] and c["trace"] == original["trace"], c["id"]
        assert c["source"]["outcome"] == original["result"]["outcome"], c["id"]
    assert all(c["source"]["outcome"] == "correct" for c in CASES if c["id"].endswith(".ok"))
    assert BY_ID["q010.raw-bare"]["source"]["outcome"] == "wrong_confident"


def test_injected_cases_differ_only_by_their_edits():
    for c in CASES:
        if c["origin"] != "injected":
            continue
        base = BY_ID[c["base_case"]]
        assert base["origin"] == "natural" and base["expected_verdict"] == "approve", c["id"]
        assert base["source_question"] == c["source_question"], c["id"]
        assert c["id"] == f"{c['source_question']}.{c['fault_type']}"
        same = ("source_question", "split", "question", "answer_schema", "source")
        assert all(c[k] == base[k] for k in same), c["id"]
        assert (c["response"], c["trace"]) != (base["response"], base["trace"]), c["id"]
        response, trace = mf.apply_edits(base["response"], base["trace"], c["edits"], c["id"])
        assert (response, trace) == (c["response"], c["trace"]), f"{c['id']}: not exactly its base plus its edits"
        assert [e.get("event") for e in c["trace"]] == [e.get("event") for e in base["trace"]], c["id"]
        for edit in c["edits"]:
            if edit["scope"] == "text" and edit["new"]:
                assert edit["new"] in c["response"], (c["id"], edit["new"][:60])
        touches_sql = any(e["scope"] == "sql" for e in c["edits"])
        sql = [e["sql"] for e in c["trace"] if e.get("event") == "sql"]
        assert (sql != [e["sql"] for e in base["trace"] if e.get("event") == "sql"]) == touches_sql, c["id"]


def test_rebuild_is_deterministic():
    if not all(detail_items(run) for run in (mf.GOOD_RUN, mf.NATURAL["run"])) or not mf.DB_PATH.is_file():
        SKIPPED.append("rebuild: local detail files or warehouse.duckdb missing")
        return
    assert mf.render(mf.build()) == CASES_TEXT, "cases.json differs from a fresh build (run make_faults.py)"


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
    for note in SKIPPED:
        print(f"SKIP {note}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
