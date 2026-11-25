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


# -- runner --heldout ------------------------------------------------------------------------------------

RUNNER = HERE.parent / "run_reviewer_eval.py"
PLAN = {  # response marker -> (verdict or "format", key fields)
    "R1": ("reject", "expected_reject", "v3-harder", "q901"), "R2": ("revise", "expected_reject", "v3-harder", "q901"),
    "R3": ("approve", "expected_reject", "abl-floor", "q902"), "R4": ("format", "expected_reject", "abl-floor", "q903"),
    "R5": ("approve", "unlabelled_correct", "v3-harder", "q904"), "R6": ("revise", "unlabelled_correct", "abl-floor", "q904"),
    "R7": ("reject", "unlabelled_correct", "abl-floor", "q901"), "R8": ("approve", "unlabelled_correct", "v3-harder", "q902"),
}
AGENTS = {
    "planned": """
import json, re, sys
case = json.loads(sys.stdin.read())
marker = re.search(r"R[0-9]", case["response"]).group()
verdict = json.loads(open(__file__.replace("planned.py", "plan.json")).read())[marker][0]
if verdict == "format":
    print(json.dumps({"verdict": None, "error": "format_error"}))
else:
    findings = [] if verdict == "approve" else [
        {"check": "definitions", "severity": "high", "evidence": "query 1", "fix": "Fix it."}]
    print(json.dumps({"verdict": verdict, "reason": "r", "findings": findings}))
print('USAGE {"input_tokens": 100, "output_tokens": 10, "model": "claude-fake"}', file=sys.stderr)
""",
    "recorder": """
import json, sys
raw = sys.stdin.read()
with open(__file__.replace("recorder.py", "stdin.jsonl"), "a") as f:
    f.write(raw.replace("\\n", " ") + "\\n")
print(json.dumps({"verdict": "approve", "reason": "ok", "findings": []}))
print('USAGE {"input_tokens": 1, "output_tokens": 1, "model": "claude-fake"}', file=sys.stderr)
""",
    "crasher": """
import sys
sys.stdin.read()
sys.exit(2)
""",
}
SUMMARY_KEYS = {"heldout", "timestamp", "label", "git_sha", "git_dirty", "scorer_version", "model", "brief_sha256",
                "reviewer_sha256", "agent_cmd", "earlier_heldout_runs", "n_cases", "expected_reject",
                "unlabelled_correct", "per_source_label", "overall", "tokens"}


class HeldoutEnv:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.repo, self.runs = self.dir / "repo", self.dir / "runs"
        self.repo.mkdir()
        git = lambda *a: subprocess.run(["git", *a], cwd=self.repo, capture_output=True, text=True, check=True)
        git("init", "-q")
        (self.repo / "README").write_text("x\n")
        git("add", "README")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")
        cases, key = [], {}
        for n, (marker, (verdict, group, label, qid)) in enumerate(sorted(PLAN.items()), 1):
            hid = f"h{n:03d}"
            cases.append({"id": hid, "question": f"{QMARK} {n}?", "answer_schema": {"answer": {"n": "integer"}},
                          "response": f"{RMARK} {marker} the answer is {n}.", "trace": sql_trace(n)})
            key[hid] = {"source_label": label, "source_run": f"run_{label}", "question_id": qid, "outcome": "x",
                        "manual_review": False, "group": group,
                        "expected_verdict": "reject" if group == "expected_reject" else None}
        self.cases, self.key = self.dir / "cases.json", self.dir / "key.json"
        self.cases.write_text(json.dumps(cases))
        self.key.write_text(json.dumps(key))
        (self.dir / "plan.json").write_text(json.dumps(PLAN))
        for name, code in AGENTS.items():
            (self.dir / f"{name}.py").write_text(code)

    def run(self, *args, agent="planned", confirm=True, heldout=True):
        argv = [sys.executable, str(RUNNER), "--runs-dir", str(self.runs), "--repo", str(self.repo),
                "--heldout-cases", str(self.cases), "--heldout-key", str(self.key),
                "--agent-cmd", f'"{sys.executable}" "{self.dir / f"{agent}.py"}"']
        argv += (["--heldout"] if heldout else []) + (["--confirm-heldout"] if confirm else []) + list(args)
        return subprocess.run(argv, capture_output=True, text=True, timeout=300, cwd=ROOT)

    def files(self):
        return sorted(self.runs.rglob("*.json")) if self.runs.exists() else []

    def close(self):
        self.tmp.cleanup()


def with_heldout(fn):
    def wrapper():
        env = HeldoutEnv()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


def no_case_content(text):
    assert QMARK not in text and RMARK not in text and "SELECT" not in text and "Fix it." not in text
    assert not re.search(r"\bh\d{3}\b", text), "opaque ids leaked"
    assert not re.search(r"\bq9\d\d\b", text), "question ids leaked"


@with_heldout
def test_heldout_refusals(env):
    p = env.run("--label", "x", confirm=False)
    assert p.returncode == 2 and "--confirm-heldout" in p.stderr and not env.files()
    (env.repo / "untracked.txt").write_text("dirty")
    p = env.run("--label", "x")
    assert p.returncode == 2 and "dirty" in p.stderr and not env.files()
    (env.repo / "untracked.txt").unlink()
    for extra in (("--label", "x", "--only", "h001"), ("--label", "x", "--show", "h001"), ()):
        p = env.run(*extra)
        assert p.returncode == 2, (extra, p.returncode)
    p = env.run("--label", "x", "--show", "h001")
    assert "unrecognized arguments: --show" in p.stderr  # no --show option exists
    p = env.run("--label", "x", "--confirm-heldout", heldout=False, confirm=False)
    assert p.returncode == 2 and "only goes with --heldout" in p.stderr
    assert not env.files()


@with_heldout
def test_heldout_scoring_and_summary(env):
    p = env.run("--label", "v2-final")
    assert p.returncode == 0, p.stderr[-800:]
    (summary_path,) = [f for f in env.files() if f.parent == env.runs]
    assert summary_path.name.endswith("_v2-final_heldout.json")
    summary = json.loads(summary_path.read_text())
    assert set(summary) == SUMMARY_KEYS, set(summary) ^ SUMMARY_KEYS
    er, uc = summary["expected_reject"], summary["unlabelled_correct"]
    assert (er["n"], er["rejected"], er["revised"], er["approved"], er["format_error"]) == (4, 1, 1, 1, 1)
    assert er["distinct_questions"] == 3 and er["approved_distinct_questions"] == 1
    assert er["rates"]["approved"] == 0.25
    assert (uc["n"], uc["approved"], uc["revised"], uc["rejected"], uc["format_error"]) == (4, 2, 1, 1, 0)
    assert "distinct_questions" not in uc
    per = summary["per_source_label"]
    assert set(per) == {"v3-harder", "abl-floor"}
    assert (per["v3-harder"]["expected_reject"]["rejected"], per["v3-harder"]["expected_reject"]["revised"]) == (1, 1)
    assert (per["abl-floor"]["expected_reject"]["approved"], per["abl-floor"]["expected_reject"]["format_error"]) == (1, 1)
    assert (per["abl-floor"]["unlabelled_correct"]["revised"], per["abl-floor"]["unlabelled_correct"]["rejected"]) == (1, 1)
    o = summary["overall"]
    assert o["format_error"] == {"n": 1, "of": 8, "rate": 0.125} and o["evidence_valid"] == {"n": 4, "of": 4, "rate": 1.0}
    assert o["invented_numbers"]["n"] == 0 and o["agent_failure"]["n"] == 0
    assert summary["tokens"] == {"input_tokens": 800, "output_tokens": 80} and summary["model"] == "claude-fake"
    assert summary["heldout"] is True and summary["earlier_heldout_runs"] == 0 and summary["git_dirty"] is False
    assert len(summary["brief_sha256"]) == len(summary["reviewer_sha256"]) == 64
    for text in (summary_path.read_text(), p.stdout, p.stderr):
        no_case_content(text)
    assert "dangerous miss" in p.stdout and "clear false alarm" in p.stdout
    detail = json.loads((env.runs / "detail" / summary_path.name).read_text())
    assert len(detail["cases"]) == 8 and detail["heldout"] is True  # per-case outputs live only in the detail file
    p2 = env.run("--label", "again")
    assert p2.returncode == 0 and json.loads(sorted(env.runs.glob("*again_heldout.json"))[0].read_text())[
        "earlier_heldout_runs"] == 1
    r = subprocess.run([sys.executable, str(RUNNER), "--runs-dir", str(env.runs), "--rescore",
                        str(env.runs / "detail" / summary_path.name)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode != 0 and "held-out detail file cannot be rescored" in r.stderr


@with_heldout
def test_heldout_agent_gets_reviewer_fields_only(env):
    p = env.run("--label", "rec", agent="recorder")
    assert p.returncode == 0, p.stderr[-500:]
    lines = (env.dir / "stdin.jsonl").read_text().splitlines()
    assert len(lines) == 8
    for line in lines:
        assert set(json.loads(line)) == {"question", "answer_schema", "response", "trace"}
        assert not re.search(r"\bh\d{3}\b", line)
        for word in KEY_WORDS:
            assert word not in line, word


@with_heldout
def test_heldout_guard(env):
    p = env.run("--label", "crash", agent="crasher")
    assert p.returncode == 3 and "summary not saved" in p.stderr and not env.files()
    no_case_content(p.stdout + p.stderr)


# -- leak check on the new text --------------------------------------------------------------------------

def test_readme_and_code_hold_no_hidden_content():
    sys.path.insert(0, str(HERE.parent))
    import test_cases as tc  # the dev cases' leak helpers: hidden ids, hidden question texts, hidden-only values
    strings, numbers = tc.hidden_only_values()
    assert len(strings) > 50 and numbers
    files = [HERE.parent / "README.md", HERE / "build_heldout.py", HERE / "test_heldout.py",
             HERE.parent / "run_reviewer_eval.py"]
    readme = (HERE.parent / "README.md").read_text()
    assert "## Held-out test" in readme
    for path in files:
        text = path.read_text()
        ids = set(re.findall(r"\bq\d{3}\b", text)) & set(tc.HIDDEN)
        assert not ids, f"{path.name}: hidden question ids {sorted(ids)}"
        flat = " ".join(text.split())
        for qid, e in tc.HIDDEN.items():
            assert " ".join(e["question"].split())[:60] not in flat, f"{path.name}: hidden question text of {qid}"
        hits = tc.leaked(text, strings, numbers)
        assert not hits, f"{path.name}: {len(hits)} hidden-only expected value(s)"  # count only
    assert not set(FAKE_IDS) & set(tc.HIDDEN), "the synthetic ids must not be real hidden ids"


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
