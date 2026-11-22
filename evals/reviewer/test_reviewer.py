"""Tests for agent/reviewer.py and evals/reviewer/run_reviewer_eval.py. No API calls: a fake client or fake agents.

Run from the repo root:  python evals/reviewer/test_reviewer.py   (or pytest evals/reviewer/test_reviewer.py)
"""
import hashlib
import io
import json
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "agent"))
import analyst  # noqa: E402
import grader  # noqa: E402
import reviewer  # noqa: E402
import run_reviewer_eval as runner  # noqa: E402

CASES = json.loads((HERE / "cases.json").read_text())
BY_ID = {c["id"]: c for c in CASES}
HIDDEN_IDS = {qid for qid, e in grader.load_questions().items() if e["split"] != "dev"}
KEY = "sk-test-secret-123"
ANSWER_KEY_WORDS = ("expected", "fault_type", "origin", "base_case", "edits", "split")
DOCS = [(ROOT / d).read_text() for d in runner.DOC_SOURCES]
GOOD = {"verdict": "reject", "reason": "Wrong filter.",
        "findings": [{"check": "definitions", "severity": "high", "evidence": "query 1", "fix": "Use delivered."}]}


# -- fakes -----------------------------------------------------------------------------------------------

def usage(i=100, o=10, cr=0, cw=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw)


def reply(text, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, usage=usage(cr=50),
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


class FakeStream:
    def __init__(self, produce):
        self.produce = produce

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.produce()


class FakeClient:
    """Streaming only, like the analyst's tests: a non-streaming call fails."""

    def __init__(self, script):
        self.script = script
        self.calls = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        i = len(self.calls) - 1
        return FakeStream(lambda: self.script(i, kwargs))


def answering(text, stop="end_turn"):
    return FakeClient(lambda i, kw: reply(text, stop))


def run_reviewer(case, client, env=None, argv=None, sleeps=None):
    out, err = io.StringIO(), io.StringIO()
    code = reviewer.main(argv=argv or [], stdin=io.StringIO(json.dumps(case)), stdout=out, stderr=err,
                         env={analyst.KEY_ENV: KEY} if env is None else env, client_factory=lambda key: client,
                         sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return code, out.getvalue(), err.getvalue()


def usage_line(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("USAGE ")]
    assert len(lines) == 1 and err.rstrip().endswith(lines[0]), err
    data = json.loads(lines[0][6:])
    assert set(data) == {"input_tokens", "output_tokens", "model"}, data
    return data


# -- reviewer: input allowlist and prompt ----------------------------------------------------------------

def test_prompt_allowlist():
    for case in CASES:
        message = reviewer.build_user_message(case)
        for word in ANSWER_KEY_WORDS:
            assert not re.search(rf"\b{word}\b", message, re.IGNORECASE), (case["id"], word)
        assert "toolu_" not in message and "tool_use_id" not in message, case["id"]
        assert case["id"] not in message, case["id"]
        only = {k: case[k] for k in reviewer.INPUT_FIELDS}
        assert reviewer.build_user_message(only) == message, case["id"]  # extra keys change nothing
        request = json.dumps(reviewer.build_request(case, analyst.MODEL), ensure_ascii=False)
        for value in (case["id"], case["fault_type"], "expected_verdict", "expected_failed_checks", "base_case",
                      "fault_type", case["source"]["sha256"]):
            if value:
                assert value not in request, (case["id"], value)
        hidden = set(re.findall(r"\bq\d{3}\b", request)) & HIDDEN_IDS
        assert not hidden, (case["id"], sorted(hidden))
        n = len(reviewer.queries(case["trace"]))
        assert n >= 1 and f"Query {n}:" in message and f"Query {n + 1}:" not in message, case["id"]


def test_main_sends_only_the_allowlisted_fields():
    case = BY_ID["q013.wrong_filter"]
    client = answering(json.dumps(GOOD))
    code, out, err = run_reviewer(case, client)  # the whole case, answer key included, on stdin
    assert code == 0, err
    sent = json.dumps(client.calls[0], ensure_ascii=False, default=str)
    for value in ("q013.wrong_filter", "wrong_filter", "expected_verdict", "base_case", "fault_type"):
        assert value not in sent, value
    assert client.calls[0]["messages"][0]["content"] == reviewer.build_user_message(case)


def test_prompt_composition():
    system = reviewer.build_system_prompt()
    positions = [system.index((ROOT / path).read_text().strip()) for _, path in reviewer.REVIEWER_DOCS]
    assert positions == sorted(positions) and positions[0] < 100, positions
    assert [p for _, p in reviewer.REVIEWER_DOCS] == ["agent/REVIEWER_BRIEF.md", "docs/metrics.md",
                                                      "evals/ANSWER_FORMAT.md"]
    notes = [ln.strip() for ln in (ROOT / "docs/data_notes.md").read_text().splitlines() if len(ln.strip()) >= 40]
    notes = [ln for ln in notes if ln not in system]  # lines shared with metrics.md do not count
    assert len(notes) > 10, "data_notes.md has nothing distinctive to check"
    full = system + reviewer.build_user_message(BY_ID["q001.ok"])
    assert not [ln for ln in notes if ln in full]
    client = answering(json.dumps(GOOD))
    code, _, err = run_reviewer(BY_ID["q001.ok"], client, env={analyst.KEY_ENV: KEY, reviewer.MODEL_ENV: "claude-x"})
    assert code == 0, err
    assert len(client.calls) == 1, "exactly one model call"
    kw = client.calls[0]
    assert "tools" not in kw and "tool_choice" not in kw and kw["model"] == "claude-x"
    assert kw["system"] == [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    assert len(kw["messages"]) == 1 and kw["messages"][0]["role"] == "user"
    assert usage_line(err) == {"input_tokens": 150, "output_tokens": 10, "model": "claude-x"}
    assert "messages.stream" not in (ROOT / "agent/reviewer.py").read_text(), "reuse analyst.call_with_retry"


# -- reviewer: output parsing and exit codes -------------------------------------------------------------

def test_parse_verdict():
    assert reviewer.parse_verdict(json.dumps(GOOD)) == GOOD
    assert reviewer.parse_verdict("  \n" + json.dumps({**GOOD, "findings": []}) + "\n") == {**GOOD, "findings": []}
    finding = GOOD["findings"][0]
    bad = {
        "prose before": "Here is my review: " + json.dumps(GOOD),
        "prose after": json.dumps(GOOD) + "\nHope this helps.",
        "code fence": "```json\n" + json.dumps(GOOD) + "\n```",
        "invalid check": json.dumps({**GOOD, "findings": [{**finding, "check": "grounding"}]}),
        "missing field": json.dumps({"verdict": "reject", "findings": []}),
        "missing finding field": json.dumps({**GOOD, "findings": [{k: v for k, v in finding.items() if k != "fix"}]}),
        "extra field": json.dumps({**GOOD, "confidence": 0.9}),
        "wrong verdict": json.dumps({**GOOD, "verdict": "approved"}),
        "null verdict": json.dumps({**GOOD, "verdict": None}),
        "bad severity": json.dumps({**GOOD, "findings": [{**finding, "severity": "critical"}]}),
        "empty reason": json.dumps({**GOOD, "reason": " "}),
        "findings not a list": json.dumps({**GOOD, "findings": {}}),
        "not an object": json.dumps([GOOD]),
        "two objects": json.dumps(GOOD) + json.dumps(GOOD),
        "empty": "",
    }
    for label, text in bad.items():
        try:
            reviewer.parse_verdict(text)
        except reviewer.FormatError:
            continue
        raise AssertionError(f"{label}: accepted")


def test_format_error_exits_zero_without_a_verdict():
    for text in ("Looks fine to me.", "```json\n" + json.dumps(GOOD) + "\n```",
                 json.dumps({**GOOD, "verdict": "maybe"})):
        code, out, err = run_reviewer(BY_ID["q001.ok"], answering(text))
        assert code == 0, err
        assert json.loads(out) == {"verdict": None, "error": "format_error"}, out
        assert "format_error" in err and text not in err  # names the rule, never echoes the reply
    code, out, _ = run_reviewer(BY_ID["q001.ok"], answering(json.dumps(GOOD)))
    assert code == 0 and json.loads(out) == GOOD


def test_failures_and_bad_input():
    for stop in ("refusal", "max_tokens"):
        code, out, err = run_reviewer(BY_ID["q001.ok"], answering(json.dumps(GOOD), stop))
        assert code == 1 and not out and stop in err, (stop, err)
        assert usage_line(err)["input_tokens"] == 150
    code, out, err = run_reviewer(BY_ID["q001.ok"], answering("  "))
    assert code == 1 and "no text" in err
    client = answering(json.dumps(GOOD))
    for env in ({analyst.KEY_ENV: KEY, reviewer.MODEL_ENV: "gpt-4"}, {analyst.KEY_ENV: KEY, reviewer.MODEL_ENV: "claude-"}):
        code, out, err = run_reviewer(BY_ID["q001.ok"], client, env=env)
        assert code == 2 and not client.calls and usage_line(err)["model"] is None, err
    code, _, err = run_reviewer(BY_ID["q001.ok"], client, env={})
    assert code == 2 and "OLIST_AGENT_API_KEY" in err and not client.calls
    code, _, err = run_reviewer({"question": "x", "response": "y", "trace": []}, client)
    assert code == 2 and "answer_schema" in err and not client.calls
    with tempfile.TemporaryDirectory() as tmp:  # a path argument instead of stdin
        path = Path(tmp) / "case.json"
        path.write_text(json.dumps(BY_ID["q005.ok"]))
        out, err = io.StringIO(), io.StringIO()
        code = reviewer.main(argv=[str(path)], stdin=io.StringIO(""), stdout=out, stderr=err,
                             env={analyst.KEY_ENV: KEY}, client_factory=lambda key: client, sleep=lambda s: None)
        assert code == 0 and json.loads(out.getvalue()) == GOOD, err.getvalue()
    assert KEY not in err


def test_retries_reuse_the_analyst_logic():
    import anthropic
    import httpx2

    def script(i, kw):
        if i == 0:
            raise anthropic.APIStatusError(
                "overloaded", response=httpx2.Response(200, request=httpx2.Request("POST", "https://api.invalid")),
                body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
        return reply(json.dumps(GOOD))
    sleeps = []
    client = FakeClient(script)
    code, out, err = run_reviewer(BY_ID["q001.ok"], client, sleeps=sleeps)
    assert code == 0 and json.loads(out) == GOOD and len(client.calls) == 2 and len(sleeps) == 1, err


# -- scoring ---------------------------------------------------------------------------------------------

TRACE = [{"event": "sql", "turn": 1, "tool_use_id": "t1", "sql": "SELECT count(*) AS n FROM fct_orders"},
         {"event": "tool_result", "tool_use_id": "t1", "content": "n\n99441\n(1 row)", "is_error": False},
         {"event": "sql", "turn": 2, "tool_use_id": "t2", "sql": "SELECT ROUND(21.4123, 2) AS rate"},
         {"event": "tool_result", "tool_use_id": "t2", "content": "rate\n21.41\n(1 row)", "is_error": False}]


def mk(expected="reject", checks=("definitions",), fault="wrong_filter"):
    return {"id": "x", "question": "Q?", "answer_schema": {}, "trace": TRACE,
            "response": 'AL has the highest rate: 21.41%. "Late means a later day."',
            "expected_verdict": expected, "expected_failed_checks": list(checks), "fault_type": fault}


def out(verdict, *checks, evidence="query 2", fix="Recheck."):
    return {"verdict": verdict, "reason": "r",
            "findings": [{"check": c, "severity": "high", "evidence": evidence, "fix": fix} for c in checks]}


def score(case, verdict=None, *checks, status="valid", **kw):
    return runner.score_case(case, status, out(verdict, *checks, **kw) if status == "valid" else None, DOCS)


def test_verdict_metrics():
    s = score(mk(), "reject", "definitions")
    assert s["verdict_match"] and s["dangerous_miss"] is False and s["soft_miss"] is False and s["check_recall"]
    assert s["false_alarm"] is None  # only approve cases count for false alarms
    s = score(mk(), "approve")
    assert s["dangerous_miss"] and not s["verdict_match"] and s["check_recall"] is False
    s = score(mk(), "revise", "definitions")
    assert s["soft_miss"] and not s["verdict_match"] and not s["dangerous_miss"] and s["check_recall"]
    s = score(mk("revise", ("assumptions",), "missing_assumption"), "reject", "assumptions")
    assert s["soft_miss"] and s["check_recall"]
    approve = mk("approve", (), None)
    s = score(approve, "approve")
    assert s["verdict_match"] and s["false_alarm"] is False and s["dangerous_miss"] is None
    assert s["check_recall"] is None and s["evidence_valid"] is None  # no expected checks, no findings
    s = score(approve, "revise", "format")
    assert s["false_alarm"] and not s["verdict_match"]


def test_check_recall_subset_rule():
    q019_style = mk("revise", ("claims_beyond_data",), "unsupported_claim")
    s = score(q019_style, "revise", "claims_beyond_data", "numbers")  # an extra flag costs nothing
    assert s["check_recall"] and s["verdict_match"]
    s = score(q019_style, "revise", "numbers")
    assert s["check_recall"] is False
    two = mk("reject", ("definitions", "assumptions"))
    assert score(two, "reject", "definitions")["check_recall"] is False
    assert score(two, "reject", "assumptions", "definitions")["check_recall"] is True


def test_invalid_outputs():
    for status in ("format_error", "agent_failure"):
        s = score(mk(), status=status)
        assert s[status] and not s["verdict_match"] and s["got"] == status
        assert s["dangerous_miss"] is False and s["check_recall"] is False and s["evidence_valid"] is None
        s = score(mk("approve", (), None), status=status)
        assert s["false_alarm"] is False


def test_evidence_rule():
    sources, n = runner.case_sources(mk(), DOCS)
    assert n == 2
    valid = ["query 2", "Query 1 counts all orders", "queries 1 and 2", "Q2", 'The response says "highest rate: 21.41%"',
             "`SELECT count(*) AS n FROM fct_orders`", "AL has the highest rate: 21.41%",
             "“Late means a later day.”"]
    invalid = ["query 3", "queries 1 and 7", 'It says "AL is late because of distance"', "The numbers look wrong",
               'query 1 and "a sentence that is not there"', "short"]
    for e in valid:
        assert runner.evidence_ok(e, sources, n), e
    for e in invalid:
        assert not runner.evidence_ok(e, sources, n), e
    s = score(mk(), "reject", "definitions", "numbers", evidence="query 9")
    assert s["evidence_valid"] is False
    assert score(mk(), "reject", "definitions", evidence="query 1")["evidence_valid"] is True


def test_invented_numbers():
    nums = runner.source_numbers(mk(), DOCS)
    ok = ["Use 21.41%.", "Rounded it is 21.4%.", "There are 99,441 orders.", "See query 7.", "Use 3 decimals.",
          "Compare with 2018."]
    bad = ["It should be 23.50%.", "Expect 7,777 late orders.", "About 88.8 percent."]
    for text in ok:
        assert not runner.invented_numbers([{"evidence": "", "fix": text}], nums), text
    for text in bad:
        assert runner.invented_numbers([{"evidence": text, "fix": ""}], nums), text
    assert score(mk(), "reject", "definitions", fix="Report 23.50% instead.")["invented_numbers"] is True
    assert score(mk(), "reject", "definitions", fix="Report 21.41%.")["invented_numbers"] is False


def test_rates():
    assert runner.rate([True, False, None, True]) == {"n": 2, "of": 3, "rate": 0.6667}
    assert runner.rate([None, None]) == {"n": 0, "of": 0, "rate": None}


# -- runner (fake agents) --------------------------------------------------------------------------------

AGENTS = {
    "perfect": """
import hashlib, json, sys
from pathlib import Path
case = json.loads(sys.stdin.read())
key = hashlib.sha256((case["question"] + case["response"]).encode()).hexdigest()
verdict, checks = json.loads((Path(__file__).parent / "answers.json").read_text())[key]
print(json.dumps({"verdict": verdict, "reason": "ok", "findings": [
    {"check": c, "severity": "high", "evidence": "query 1", "fix": "Fix it."} for c in checks]}))
print('USAGE {"input_tokens": 1000, "output_tokens": 100, "model": "claude-fake"}', file=sys.stderr)
""",
    "recorder": """
import json, sys
from pathlib import Path
raw = sys.stdin.read()
with open(Path(__file__).parent / "stdin.jsonl", "a") as f:
    f.write(raw.replace("\\n", " ") + "\\n")
print(json.dumps({"verdict": "approve", "reason": "ok", "findings": []}))
print('USAGE {"input_tokens": 5, "output_tokens": 1, "model": "claude-fake"}', file=sys.stderr)
""",
    "crasher": """
import sys
sys.stdin.read()
print('USAGE {"input_tokens": 0, "output_tokens": 0, "model": null}', file=sys.stderr)
sys.exit(2)
""",
    "formatter": """
import json, sys
sys.stdin.read()
print(json.dumps({"verdict": None, "error": "format_error"}))
print('USAGE {"input_tokens": 10, "output_tokens": 2, "model": "claude-fake"}', file=sys.stderr)
""",
}
SUMMARY_KEYS = {"timestamp", "label", "git_sha", "git_dirty", "agent_cmd", "cases_file", "n_cases", "only",
                "agent_config", "metrics", "per_fault_type", "per_check", "confusion", "tokens"}


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.runs = self.dir / "runs"
        for name, code in AGENTS.items():
            (self.dir / f"{name}.py").write_text(code)
        answers = {hashlib.sha256((c["question"] + c["response"]).encode()).hexdigest():
                   [c["expected_verdict"], c["expected_failed_checks"]] for c in CASES}
        (self.dir / "answers.json").write_text(json.dumps(answers))

    def run(self, *args, agent="perfect"):
        argv = [sys.executable, str(HERE / "run_reviewer_eval.py"), "--runs-dir", str(self.runs),
                "--agent-cmd", f'"{sys.executable}" "{self.dir / f"{agent}.py"}"', *args]
        return subprocess.run(argv, capture_output=True, text=True, timeout=600, cwd=ROOT)

    def latest(self, proc):
        (path,) = re.findall(r"^summary: (.+)$", proc.stdout, re.MULTILINE)
        path = Path(path)
        return json.loads(path.read_text()), json.loads((self.runs / "detail" / path.name).read_text())

    def files(self):
        return sorted(self.runs.rglob("*.json")) if self.runs.exists() else []

    def close(self):
        self.tmp.cleanup()


def with_env(fn):
    def wrapper():
        env = Env()
        try:
            fn(env)
        finally:
            env.close()
    wrapper.__name__ = fn.__name__
    return wrapper


@with_env
def test_runner_perfect_agent(env):
    p = env.run("--label", "perfect")
    assert p.returncode == 0, p.stderr[-800:]
    summary, detail = env.latest(p)
    n = len(CASES)
    n_problem = sum(c["expected_verdict"] != "approve" for c in CASES)
    m = summary["metrics"]
    assert m["verdict_match"] == {"n": n, "of": n, "rate": 1.0}
    assert m["dangerous_miss"] == {"n": 0, "of": n_problem, "rate": 0.0}
    assert m["false_alarm"] == {"n": 0, "of": n - n_problem, "rate": 0.0}
    assert m["check_recall"] == {"n": n_problem, "of": n_problem, "rate": 1.0}
    assert m["evidence_valid"]["n"] == m["evidence_valid"]["of"] == n_problem
    assert m["invented_numbers"]["n"] == 0 and m["format_error"]["n"] == 0
    assert summary["tokens"] == {"input_tokens": 1000 * n, "output_tokens": 100 * n}
    assert summary["agent_config"] == {"model": "claude-fake"}
    assert all(r["recalled"] == r["expected"] >= 2 for r in summary["per_check"].values())
    assert set(summary["per_fault_type"]) == {c["fault_type"] for c in CASES if c["fault_type"]} | \
        {"natural_approve", "natural_reject"}
    assert sum(sum(row.values()) for row in summary["confusion"].values()) == n
    assert "dangerous_miss" in p.stdout and "tokens:" in p.stdout
    assert len(detail["cases"]) == n and all(c["output"]["verdict"] for c in detail["cases"])


@with_env
def test_runner_sends_only_allowlisted_fields(env):
    p = env.run("--label", "rec", "--only", "q013.wrong_filter,q010.raw-bare,q020.ok", agent="recorder")
    assert p.returncode == 0, p.stderr[-800:]
    lines = (env.dir / "stdin.jsonl").read_text().splitlines()
    assert len(lines) == 3
    for line in lines:
        data = json.loads(line)
        assert set(data) == set(reviewer.INPUT_FIELDS), set(data)
        for value in ("expected_verdict", "expected_failed_checks", "fault_type", "base_case", "edits", "origin",
                      "q013.wrong_filter", "q010.raw-bare", "q020.ok", "wrong_filter"):
            assert value not in line, value


@with_env
def test_runner_subset_and_summary_schema(env):
    p = env.run("--label", "sub", "--only", "q001.ok,q019.unsupported_claim")
    assert p.returncode == 0, p.stderr[-800:]
    summary, detail = env.latest(p)
    assert set(summary) == SUMMARY_KEYS, set(summary) ^ SUMMARY_KEYS
    assert summary["n_cases"] == 2 and summary["only"] == ["q001.ok", "q019.unsupported_claim"]
    assert set(summary["metrics"]) == set(runner.METRICS)
    assert all(set(r) == {"n", "of", "rate"} for r in summary["metrics"].values())
    assert summary["metrics"]["false_alarm"]["of"] == 1 and summary["metrics"]["dangerous_miss"]["of"] == 1
    text = json.dumps(summary)
    assert "findings" not in text and "Fix it." not in text and BY_ID["q001.ok"]["response"][:40] not in text
    assert [c["id"] for c in detail["cases"]] == ["q001.ok", "q019.unsupported_claim"]
    bad = env.run("--label", "x", "--only", "q001.ok,nope")
    assert bad.returncode == 2 and "unknown case ids" in bad.stderr


@with_env
def test_runner_guard(env):
    p = env.run("--label", "crash", "--only", "q001.ok,q013.ok,q019.ok", agent="crasher")
    assert p.returncode == 3, (p.returncode, p.stderr[-500:])
    assert "every case failed before reaching the model; summary not saved" in p.stderr
    assert "exit code 2" in p.stderr and not env.files()


@with_env
def test_runner_counts_format_errors(env):
    p = env.run("--label", "fmt", "--only", "q001.ok,q013.wrong_filter,q014.false_premise_accepted",
                agent="formatter")
    assert p.returncode == 0, p.stderr[-500:]
    summary, _ = env.latest(p)
    m = summary["metrics"]
    assert m["format_error"] == {"n": 3, "of": 3, "rate": 1.0} and m["verdict_match"]["n"] == 0
    assert m["dangerous_miss"]["n"] == 0 and m["agent_failure"]["n"] == 0
    assert summary["confusion"]["reject"]["format_error"] == 2


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
