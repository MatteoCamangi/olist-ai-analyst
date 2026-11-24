"""Tests for evals/reviewer/cases.json, the reviewer agent's dev-only test cases.

Run from the repo root:  python evals/reviewer/test_cases.py   (or pytest evals/reviewer/test_cases.py)
Needs no database. Comparisons with the original runs need the local detail files (gitignored) and say when they
are skipped.
"""
import hashlib
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
# the brief's verdict rules applied per fault: reject when the headline becomes wrong or unsupported
VERDICT_BY_FAULT = {"wrong_filter": "reject", "threshold_off_by_one": "reject", "number_mismatch": "reject",
                    "false_refusal": "reject", "false_premise_accepted": "reject", "unsupported_claim": "revise",
                    "invented_cause": "revise", "missing_assumption": "revise", "contradicting_assumption": "revise",
                    "format_break": "revise"}
FIRST_17_SHA256 = "66d89b876187d6cc17af17edfc3c65d539bca794a794c2be6c71c19bc2df65f5"  # cases.json at a4fb323
FIRST_17_IDS = ["q001.ok", "q001.threshold_off_by_one", "q002.ok", "q005.ok", "q010.invented_cause", "q010.ok",
                "q010.raw-bare", "q013.ok", "q013.wrong_filter", "q014.ok", "q017.ok", "q018.missing_assumption",
                "q018.ok", "q019.ok", "q019.unsupported_claim", "q020.contradicting_assumption", "q020.ok"]
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
    assert len(CASES) == len(BY_ID) == 29, len(CASES)
    ids = [c["id"] for c in CASES]
    assert ids[:17] == sorted(ids[:17]) and ids[17:] == sorted(ids[17:]), "each batch is sorted by id"
    for c in CASES:
        allowed = set(FIELDS) | ({"label_history"} if c["id"] in mf.LABEL_CORRECTIONS else set())
        assert set(c) == allowed, (c["id"], set(c) ^ allowed)
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
    assert {c["fault_type"] for c in CASES if c["fault_type"]} == set(mf.FAULT_TYPES)
    natural = [c for c in CASES if c["origin"] == "natural"]
    assert sum(c["expected_verdict"] == "approve" for c in natural) == 7
    assert sorted(c["id"] for c in natural if c["expected_verdict"] == "revise") == ["q001.ok", "q010.ok", "q018.ok"]
    assert BY_ID["q010.raw-bare"]["expected_verdict"] == "reject"
    assert BY_ID["q010.raw-bare"]["expected_failed_checks"] == ["definitions"]


def undo_corrections(cases):
    """The cases as they were before the recorded label corrections."""
    out = []
    for c in cases:
        c = json.loads(json.dumps(c))
        history = c.pop("label_history", None)
        if history:
            c.update(history[0]["from"])
        out.append(c)
    return out


def test_first_17_cases_unchanged():
    """Only the recorded label corrections changed the first 17 cases: undoing them gives the frozen bytes."""
    assert [c["id"] for c in CASES[:17]] == FIRST_17_IDS
    blob = json.dumps(undo_corrections(CASES[:17]), indent=2, ensure_ascii=False).encode()
    assert hashlib.sha256(blob).hexdigest() == FIRST_17_SHA256, "the first 17 cases changed beyond the corrections"
    current = json.dumps(CASES[:17], indent=2, ensure_ascii=False).encode()
    assert hashlib.sha256(current).hexdigest() != FIRST_17_SHA256
    assert CASES_TEXT.startswith(mf.render(CASES[:17])[:-3]), "the first 17 cases are not a prefix of the file"


def test_label_corrections():
    corrected = [c for c in CASES if c.get("label_history")]
    assert sorted(c["id"] for c in corrected) == ["q001.ok", "q010.ok", "q018.ok"] == sorted(mf.LABEL_CORRECTIONS)
    sources = {"q001.ok": "corrected after reading reviewer v1 findings",
               "q018.ok": "corrected after reading reviewer v1 findings",
               "q010.ok": "found by the v2 runs after reading findings; v1 had approved it"}
    for c in corrected:
        (entry,) = c["label_history"]
        assert set(entry) == {"date", "from", "to", "reason", "source"}, entry
        assert entry["date"] == "2026-11-24" and entry["source"] == sources[c["id"]], c["id"]
        assert entry["from"]["expected_verdict"] == "approve" and entry["from"]["expected_failed_checks"] == []
        assert entry["to"] == {k: c[k] for k in ("expected_verdict", "expected_failed_checks", "note")}
        assert c["expected_verdict"] == "revise" and c["expected_failed_checks"] == ["claims_beyond_data"]
        assert c["origin"] == "natural" and len(entry["reason"]) > 40
    for c in CASES:
        if c["origin"] == "injected":
            assert "label_history" not in c, c["id"]  # injected cases on corrected bases stay unchanged


def test_fault_variants():
    by_type = {}
    for c in CASES:
        if c["fault_type"]:
            by_type.setdefault(c["fault_type"], []).append(c)
    assert set(by_type) == set(mf.FAULT_TYPES) and len(mf.FAULT_TYPES) == 10
    variants = {"false_refusal": 1, "false_premise_accepted": 1}  # every other type has two variants
    for fault, cases in by_type.items():
        assert len(cases) == variants.get(fault, 2), (fault, len(cases))
        assert len({c["source_question"] for c in cases}) == len(cases), f"{fault}: two variants on one base"
    for fault in ("wrong_filter", "threshold_off_by_one", "unsupported_claim", "invented_cause",
                  "missing_assumption", "contradicting_assumption"):
        assert sum(c["id"] in FIRST_17_IDS for c in by_type[fault]) == 1, fault
    silent = BY_ID["q018.wrong_filter"]
    assumptions = json.loads(silent["response"].split("```json", 1)[1].split("```", 1)[0])["assumptions"]
    assert any("except canceled" in a for a in assumptions), "silent wrong_filter: assumptions match the SQL"
    assert not any("delivered" in a.lower() for a in assumptions)
    assert "delivered" not in silent["question"].lower(), "silent: the question must not name the population"


def test_every_brief_check_has_two_cases():
    brief = (ROOT / "agent" / "REVIEWER_BRIEF.md").read_text()
    section_3 = brief.split("## 3. Checks", 1)[1].split("\n## 4.", 1)[0]
    names = re.findall(r"^- ([A-Z][^:]+):", section_3, re.MULTILINE)
    assert len(names) == len(mf.CHECKS) == 7, names
    slugs = {"Numbers": "numbers", "Definitions": "definitions", "Grain and population": "grain_population",
             "Assumptions": "assumptions", "Refusals and false premises": "refusals_false_premises",
             "Claims beyond the data": "claims_beyond_data", "Format": "format"}
    assert [slugs[n] for n in names] == list(mf.CHECKS), names
    for check in mf.CHECKS:
        n = sum(check in c["expected_failed_checks"] for c in CASES)
        assert n >= 2, (check, n)
    counts = {v: sum(c["expected_verdict"] == v for c in CASES) for v in mf.VERDICTS}
    assert counts == {"approve": 7, "revise": 13, "reject": 9}, counts


def test_verdicts_follow_the_brief():
    brief = (ROOT / "agent" / "REVIEWER_BRIEF.md").read_text()
    assert "Verdict: approve | revise | reject" in brief
    for check in ("Numbers", "Definitions", "Grain and population", "Assumptions", "Refusals and false premises",
                  "Claims beyond the data", "Format"):
        assert f"- {check}:" in brief, check
    assert len(mf.CHECKS) == 7
    for c in CASES:
        if c["origin"] == "injected":
            assert c["expected_verdict"] == VERDICT_BY_FAULT[c["fault_type"]], (c["id"], c["expected_verdict"])


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
        base_label = (base.get("label_history") or [{"from": base}])[0]["from"]["expected_verdict"]
        assert base["origin"] == "natural" and base_label == "approve", c["id"]  # approve when injected
        assert base["source_question"] == c["source_question"], c["id"]
        assert c["id"] == f"{c['source_question']}.{c['fault_type']}"
        same = ("source_question", "split", "question", "answer_schema", "source")
        assert all(c[k] == base[k] for k in same), c["id"]
        assert (c["response"], c["trace"]) != (base["response"], base["trace"]), c["id"]
        response, trace = mf.apply_edits(base["response"], base["trace"], c["edits"], c["id"])
        assert (response, trace) == (c["response"], c["trace"]), f"{c['id']}: not exactly its base plus its edits"
        assert [e.get("event") for e in c["trace"]] == [e.get("event") for e in base["trace"]], c["id"]
        for edit in c["edits"]:
            if edit["scope"] in ("text", "text_all") and edit["new"]:
                assert edit["new"] in c["response"], (c["id"], edit["new"][:60])
        final = [e["text"] for e in c["trace"] if e.get("event") == "final"]
        assert final == [c["response"].rstrip("\n")], f"{c['id']}: final event and response disagree"
        touches_sql = any(e["scope"] == "sql" for e in c["edits"])
        sql = [e["sql"] for e in c["trace"] if e.get("event") == "sql"]
        assert (sql != [e["sql"] for e in base["trace"] if e.get("event") == "sql"]) == touches_sql, c["id"]


def test_sql_check_tolerates_only_float_noise():
    assert mf._same_cell("13221498.11000009", "13221498.11000013")  # parallel float sum, last digits vary
    for a, b in (("13.27", "13.28"), ("6601", "6610"), ("1255695.13", "1233131.72"), ("AL", "MA"), ("0", "")):
        assert not mf._same_cell(a, b), (a, b)
    assert not mf._same_row(["1", "2"], ["1"])


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
