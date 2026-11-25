"""Tests for agent/pipeline.py with a fake analyst and a fake reviewer (small scripts in a temp dir). No API calls.

Run from the repo root:  python agent/test_pipeline.py   (or pytest agent/test_pipeline.py)
"""
import io
import json
import re
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pipeline  # noqa: E402

PAYLOAD = {"question": "How many orders were canceled?", "answer_schema": {"answer": {"n": "integer"}}}
ANSWER_1 = 'There were 625 canceled orders (0.63%).\n\n```json\n{"status": "answered", "assumptions": [], ' \
           '"answer": {"n": 625}}\n```\n'
ANSWER_2 = 'There were 1,234 canceled orders (1.24%).\n\n```json\n{"status": "answered", "assumptions": ' \
           '["canceled only"], "answer": {"n": 1234}}\n```\n'
REFUSAL = 'The data cannot answer this.\n\n```json\n{"status": "cannot_answer", "assumptions": [], "answer": null, ' \
          '"reason": "No 2019 data."}\n```\n'
FINDING = {"check": "numbers", "severity": "high", "evidence": 'query 1 and "625"', "fix": "Recompute the count."}

FAKE_ANALYST = r'''
import json, os, sys
from pathlib import Path
here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text())
payload = json.loads(sys.stdin.read())
counter = here / "analyst.count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
fb = os.environ.get("ANALYST_FEEDBACK_FILE")
with open(here / "analyst.log", "a") as f:
    f.write(json.dumps({"payload": payload, "feedback": Path(fb).read_text() if fb else None,
                        "env": {k: os.environ.get(k) for k in ("ANALYST_MODEL", "ANALYST_DOCS", "ANALYST_BRIEF",
                                                               "ANALYST_SCOPE", "REVIEWER_MODEL", "OLIST_AGENT_API_KEY",
                                                               "PIPELINE_RESULT_FILE")}}) + "\n")
with open(os.environ["AGENT_TRACE_FILE"], "a") as f:
    for e in ({"event": "sql", "turn": 1, "tool_use_id": f"toolu_{n}", "sql": f"SELECT {n + 1} AS n"},
              {"event": "tool_result", "tool_use_id": f"toolu_{n}", "content": f"n\n{n + 1}\n(1 row)", "is_error": False},
              {"event": "text", "turn": 2, "text": "thinking aloud"}, {"event": "final", "text": "final text"}):
        f.write(json.dumps(e) + "\n")
print('USAGE ' + json.dumps({"input_tokens": 1000, "output_tokens": 100, "tool_calls": 1, "model": "claude-a",
                             "docs": "full", "brief": "full", "scope": "marts"}), file=sys.stderr)
exits = plan.get("analyst_exit", [])
if n < len(exits) and exits[n]:
    sys.exit(exits[n])
sys.stdout.write(plan["analyst"][min(n, len(plan["analyst"]) - 1)])
'''

FAKE_REVIEWER = r'''
import json, sys
from pathlib import Path
here = Path(__file__).parent
plan = json.loads((here / "plan.json").read_text())
raw = sys.stdin.read()
with open(here / "reviewer.log", "a") as f:
    f.write(raw.replace("\n", " ") + "\n")
counter = here / "reviewer.count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
print('USAGE {"input_tokens": 300, "output_tokens": 30, "model": "claude-r"}', file=sys.stderr)
review = plan["reviews"][n]
if review == "crash":
    sys.exit(1)
if review == "format":
    print(json.dumps({"verdict": None, "error": "format_error"}))
else:
    print(json.dumps(review))
'''


def review(verdict, *findings):
    return {"verdict": verdict, "reason": "r", "findings": list(findings)}


class Env:
    def __init__(self, plan):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "plan.json").write_text(json.dumps(plan))
        (self.dir / "analyst.py").write_text(FAKE_ANALYST)
        (self.dir / "reviewer.py").write_text(FAKE_REVIEWER)
        self.result = self.dir / "result.json"
        self.trace = self.dir / "outer_trace.jsonl"

    def run(self, **extra_env):
        env = {"PIPELINE_ANALYST_CMD": f'"{sys.executable}" "{self.dir / "analyst.py"}"',
               "PIPELINE_REVIEWER_CMD": f'"{sys.executable}" "{self.dir / "reviewer.py"}"',
               "PIPELINE_RESULT_FILE": str(self.result), "AGENT_TRACE_FILE": str(self.trace),
               "PATH": "/usr/bin:/bin", **extra_env}
        out, err = io.StringIO(), io.StringIO()
        code = pipeline.main(stdin=io.StringIO(json.dumps(PAYLOAD)), stdout=out, stderr=err, env=env)
        side = json.loads(self.result.read_text()) if self.result.exists() else None
        return code, out.getvalue(), err.getvalue(), side

    def log(self, name):
        path = self.dir / name
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def close(self):
        self.tmp.cleanup()


def with_plan(plan):
    def deco(fn):
        def wrapper():
            env = Env(plan)
            try:
                fn(env)
            finally:
                env.close()
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def usage_of(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("USAGE ")]
    assert len(lines) == 1 and err.rstrip().endswith(lines[0]), err
    return json.loads(lines[0][6:])


SIDE_KEYS = {"config", "status", "attempts", "shipped_response", "withheld", "handoff", "stakeholder_message", "tokens"}


# -- verdict paths ---------------------------------------------------------------------------------------

@with_plan({"analyst": [ANSWER_1], "reviews": [review("approve")]})
def test_approve_path(env):
    code, out, err, side = env.run()
    assert code == 0 and out == ANSWER_1
    assert side["status"] == "approved" and len(side["attempts"]) == 1 and side["shipped_response"] == ANSWER_1
    assert side["withheld"] is False and side["handoff"] is None and side["stakeholder_message"] is None
    u = usage_of(err)
    assert (u["attempts"], u["status"], u["input_tokens"], u["output_tokens"]) == (1, "approved", 1300, 130)
    assert len(env.log("analyst.log")) == 1 and env.log("analyst.log")[0]["feedback"] is None


@with_plan({"analyst": [ANSWER_1, ANSWER_2], "reviews": [review("revise", FINDING), review("approve")]})
def test_revise_retry_approve(env):
    code, out, err, side = env.run()
    assert code == 0 and out == ANSWER_2
    assert side["status"] == "approved_after_retry" and [a["review"]["verdict"] for a in side["attempts"]] == \
        ["revise", "approve"]
    calls = env.log("analyst.log")
    assert calls[0]["feedback"] is None and calls[1]["payload"] == PAYLOAD
    fb = calls[1]["feedback"]
    for part in ("check: numbers", "severity: high", 'evidence: query 1 and "625"', "fix: Recompute the count.",
                 "Verify every finding with your own queries before changing anything",
                 "Do not use any number you did not compute yourself",
                 "If a finding is wrong, say so and keep your answer"):
        assert part in fb, part
    u = usage_of(err)
    assert (u["analyst_input_tokens"], u["reviewer_input_tokens"], u["input_tokens"]) == (2000, 600, 2600)
    assert (u["analyst_output_tokens"], u["reviewer_output_tokens"], u["tool_calls"]) == (200, 60, 2)


@with_plan({"analyst": [ANSWER_1, ANSWER_2],
            "reviews": [review("revise", FINDING), review("revise", {**FINDING, "evidence": '"1,234"'})]})
def test_revise_twice_is_withheld(env):
    code, out, err, side = env.run()
    assert code == 0
    marker = json.loads(out)
    assert set(marker) == {"status", "withheld", "stakeholder_message"}
    assert marker["status"] == "needs_human_review" and marker["withheld"] is True
    assert "625" not in out and "1,234" not in out and "1.24" not in out
    assert side["status"] == "needs_human_review" and side["shipped_response"] is None and side["withheld"] is True
    h = side["handoff"]
    assert h["answer"] == ANSWER_2 and h["findings"][0]["evidence"] == '"1,234"'
    assert h["queries"] == [{"sql": "SELECT 2 AS n", "result": "n\n2\n(1 row)"}]
    assert side["stakeholder_message"] == marker["stakeholder_message"]


@with_plan({"analyst": [ANSWER_1, ANSWER_2],
            "reviews": [review("reject", FINDING), review("reject", FINDING)]})
def test_reject_path(env):
    code, out, err, side = env.run()
    assert code == 0 and json.loads(out)["withheld"] is True
    assert [a["review"]["verdict"] for a in side["attempts"]] == ["reject", "reject"]
    assert len(env.log("analyst.log")) == 2  # reject gets the same single retry as revise


@with_plan({"analyst": [ANSWER_1], "reviews": ["format"]})
def test_reviewer_format_error_fails_closed(env):
    code, out, err, side = env.run()
    assert code == 0 and json.loads(out)["status"] == "needs_human_review"
    assert len(side["attempts"]) == 1 and side["attempts"][0]["review"] == {"verdict": None, "error": "format_error"}
    assert len(env.log("analyst.log")) == 1, "no retry without findings"
    assert side["handoff"]["answer"] == ANSWER_1 and side["handoff"]["findings"] == []


@with_plan({"analyst": [ANSWER_1], "reviews": ["crash"]})
def test_reviewer_failure_fails_closed(env):
    code, out, err, side = env.run()
    assert code == 0 and json.loads(out)["status"] == "needs_human_review"
    assert side["attempts"][0]["review"]["error"] == "agent_failure" and len(env.log("analyst.log")) == 1


@with_plan({"analyst": [ANSWER_1, ANSWER_2], "analyst_exit": [0, 1], "reviews": [review("revise", FINDING)]})
def test_failed_retry_fails_closed(env):
    code, out, err, side = env.run()
    assert code == 0 and json.loads(out)["status"] == "needs_human_review"
    assert side["handoff"]["answer"] == ANSWER_1 and side["attempts"][1]["response"] is None


@with_plan({"analyst": [ANSWER_1], "analyst_exit": [1], "reviews": []})
def test_first_analyst_failure_exits_1(env):
    code, out, err, side = env.run()
    assert code == 1 and out == "" and side["status"] == "analyst_failed" and usage_of(err)["attempts"] == 1
    assert not env.log("reviewer.log")


@with_plan({"analyst": [ANSWER_1], "reviews": [review("revise", FINDING)]})
def test_zero_retries_and_bad_config(env):
    code, out, err, side = env.run(PIPELINE_MAX_RETRIES="0")
    assert code == 0 and json.loads(out)["withheld"] is True and len(side["attempts"]) == 1
    assert side["config"]["pipeline_max_retries"] == 0
    for bad in ("-1", "two"):
        code, _, err, _ = env.run(PIPELINE_MAX_RETRIES=bad)
        assert code == 2 and "PIPELINE_MAX_RETRIES" in err


@with_plan({"analyst": [REFUSAL], "reviews": [review("approve")]})
def test_approved_cannot_answer_passes_through(env):
    code, out, err, side = env.run()
    assert code == 0 and out == REFUSAL, "an approved refusal is shipped untouched"
    assert side["status"] == "approved" and side["withheld"] is False and side["stakeholder_message"] is None


# -- isolation, side file, message -----------------------------------------------------------------------

@with_plan({"analyst": [ANSWER_1, ANSWER_2], "reviews": [review("revise", FINDING), review("approve")]})
def test_reviewer_input_allowlist_and_env_passthrough(env):
    code, _, _, _ = env.run(ANALYST_MODEL="claude-x", ANALYST_DOCS="none", ANALYST_BRIEF="minimal",
                            ANALYST_SCOPE="raw", REVIEWER_MODEL="claude-y", OLIST_AGENT_API_KEY="k")
    assert code == 0
    inputs = env.log("reviewer.log")
    assert len(inputs) == 2
    for data in inputs:
        assert set(data) == {"question", "answer_schema", "response", "trace"}
        assert {e["event"] for e in data["trace"]} == {"sql", "tool_result"}  # text/final dropped
        text = json.dumps(data)
        for word in ("expected", "fault_type", "origin", "base_case", "status_", "attempt", "PIPELINE"):
            assert word not in text, word
    assert inputs[1]["response"] == ANSWER_2 and inputs[1]["trace"][0]["sql"] == "SELECT 2 AS n"
    for call in env.log("analyst.log"):
        assert call["env"] == {"ANALYST_MODEL": "claude-x", "ANALYST_DOCS": "none", "ANALYST_BRIEF": "minimal",
                               "ANALYST_SCOPE": "raw", "REVIEWER_MODEL": "claude-y", "OLIST_AGENT_API_KEY": "k",
                               "PIPELINE_RESULT_FILE": None}
    side = json.loads(env.result.read_text())
    assert side["config"]["reviewer_model"] == "claude-r"
    outer = [json.loads(line) for line in env.trace.read_text().splitlines()]
    assert [e["attempt"] for e in outer if e["event"] == "sql"] == [1, 2]
    assert [e["verdict"] for e in outer if e["event"] == "review"] == ["revise", "approve"]


@with_plan({"analyst": [ANSWER_1, ANSWER_2], "reviews": [review("revise", FINDING), review("approve")]})
def test_side_file_schema(env):
    _, _, _, side = env.run()
    assert set(side) == SIDE_KEYS
    assert set(side["config"]) == {"model", "docs", "brief", "scope", "reviewer_model", "pipeline_max_retries"}
    for a in side["attempts"]:
        assert set(a) == {"attempt", "response", "analyst_exit", "review", "tokens"}
        assert set(a["tokens"]) == {"analyst", "reviewer"}
    assert side["tokens"] == {"analyst": {"input_tokens": 2000, "output_tokens": 200, "tool_calls": 2},
                              "reviewer": {"input_tokens": 600, "output_tokens": 60}}


def test_stakeholder_message():
    def msg(*findings):
        return pipeline.stakeholder_message(list(findings))
    base = msg()
    for text in (base, msg(FINDING), msg({**FINDING, "check": "definitions"})):
        assert not re.search(r"\d", text), "no digits at all"
        for part in ("could not produce a verified answer", "run twice", "could not confirm it",
                     "no figure is being shared", "human analyst will follow up with the full working"):
            assert part in text, part
    assert "definition or population could not be confirmed against the metric documentation" in \
        msg({"check": "definitions", "severity": "high"}) == msg({"check": "grain_population", "severity": "medium"})
    assert "figures could not be traced to the queries" in msg({"check": "numbers", "severity": "low"})
    for check in ("claims_beyond_data", "assumptions", "format", "refusals_false_premises"):
        assert "some statements could not be supported by the data" in msg({"check": check, "severity": "medium"})
    # the most severe finding decides, whatever its position
    mixed = msg({"check": "format", "severity": "low"}, {"check": "numbers", "severity": "high"})
    assert "figures could not be traced" in mixed
    assert ":" not in base  # no reason clause without findings
    # nothing copied from the withheld answer or the findings: no number, no run of three words
    withheld = ANSWER_2 + " " + json.dumps(FINDING)
    words = re.findall(r"[A-Za-z0-9_.,%]+", withheld)
    for text in (msg(FINDING), msg({**FINDING, "check": "definitions"})):
        for number in re.findall(r"\d[\d,.]*", withheld):
            assert number not in text, number
        lowered = " ".join(re.findall(r"[a-z0-9_.,%]+", text.lower()))
        for i in range(len(words) - 2):
            run = " ".join(words[i:i + 3]).lower()
            assert run not in lowered, run


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
