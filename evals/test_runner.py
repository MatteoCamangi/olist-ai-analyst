"""Tests for evals/run_eval.py, with fake agents written as small scripts in a temp dir.

Run from the repo root:  python evals/test_runner.py   (or pytest evals/test_runner.py)
Runs write to a temp runs dir, never to evals/runs. Failure messages never show expected values.
"""
import json
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_grader  # noqa: E402
from test_grader import Q, assert_no_leak, correct_responses  # noqa: E402

RUNNER = HERE / "run_eval.py"
ID = re.compile(r"\bq\d{3}\b")

AGENTS = {
    "perfect": """
import json, sys
from pathlib import Path
q = json.loads(sys.stdin.read())
print(json.loads((Path(__file__).parent / "responses.json").read_text())[q["question"]])
""",
    "refuser": """
import json, sys
json.loads(sys.stdin.read())
print("I cannot answer this from the data.\\n\\n```json\\n" + json.dumps(
    {"status": "cannot_answer", "assumptions": [], "answer": None,
     "reason": "The data cannot answer this question."}) + "\\n```")
""",
    "crasher": """
import sys
sys.stdin.read()
sys.exit(1)
""",
    "sleeper": """
import sys, time
sys.stdin.read()
time.sleep(30)
""",
    "echo": """
import sys
print("payload: " + sys.stdin.read())
""",
}


def perfect_map():
    """question text -> correct response; q016 uses the first accepted interpretation (by_revenue)."""
    return {e["question"]: next(t for _, t in correct_responses(e)) for e in Q.values()}


class Env:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.runs = self.dir / "runs"
        for name, code in AGENTS.items():
            (self.dir / f"{name}.py").write_text(code)
        (self.dir / "responses.json").write_text(json.dumps(perfect_map()))

    def cmd(self, agent):
        return f'"{sys.executable}" "{self.dir / f"{agent}.py"}"'

    def run(self, *args, agent=None):
        argv = [sys.executable, str(RUNNER), "--runs-dir", str(self.runs), *args]
        if agent:
            argv += ["--agent-cmd", self.cmd(agent)]
        p = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        return p

    def summaries(self):
        return sorted(self.runs.glob("*.json")) if self.runs.exists() else []

    def latest(self, proc):
        """Summary and detail of the run whose console output is proc.stdout."""
        (path,) = re.findall(r"^summary: (.+)$", proc.stdout, re.MULTILINE)
        path = Path(path)
        return json.loads(path.read_text()), json.loads((self.runs / "detail" / path.name).read_text())

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


def ok(p, label):
    assert p.returncode == 0, f"{label}: exit {p.returncode}: {p.stderr[-500:]}"


# ---------------------------------------------------------------------------------------------------------

@with_env
def test_perfect_agent_scores_100(env):
    for split in ("dev", "hidden", "all"):
        extra = ["--confirm-hidden"] if split != "dev" else []
        p = env.run("--split", split, "--label", f"perfect-{split}", *extra, agent="perfect")
        ok(p, split)
        summary, detail = env.latest(p)
        assert summary["split"] == split
        for s, rates in summary["rates"].items():
            assert rates["accuracy"] == 1.0, f"{split}/{s}: accuracy {rates['accuracy']}"
        n = sum(split in ("all", e["split"]) for e in Q.values())
        assert summary["n_questions"] == n and len(detail["questions"]) == n
        assert_no_leak(summary, f"{split} summary")
        if split != "dev":
            assert not ID.search(json.dumps(summary)), f"{split} summary holds question ids"
            assert not ID.search(p.stdout), f"{split} console holds question ids"
    hidden_runs = [json.loads(x.read_text()).get("earlier_hidden_runs") for x in env.summaries()]
    assert sorted(h for h in hidden_runs if h is not None) == [0, 1], hidden_runs


@with_env
def test_always_refusing_agent(env):
    p = env.run("--split", "all", "--label", "refuser", "--confirm-hidden", agent="refuser")
    ok(p, "refuser")
    summary, detail = env.latest(p)
    for s, r in summary["rates"].items():
        assert r["false_refusal_rate"] == 1.0, s
        assert r["correct"] <= r["questions"] - r["answerable"], s
    print("       always-refusing agent: " + json.dumps(summary["rates"]))


@with_env
def test_crashing_agent_is_format_error(env):
    p = env.run("--split", "dev", "--label", "crash", agent="crasher")
    ok(p, "crash")
    summary, detail = env.latest(p)
    assert summary["rates"]["dev"]["format_error_rate"] == 1.0
    for q in detail["questions"]:
        assert q["result"]["outcome"] == "format_error"
        assert q["result"]["reasons"] == ["agent failed: exit code 1"], q["result"]["reasons"]


@with_env
def test_timeout_is_format_error(env):
    p = env.run("--split", "dev", "--label", "slow", "--timeout", "1", "--questions", "q001", agent="sleeper")
    ok(p, "timeout")
    _, detail = env.latest(p)
    (q,) = detail["questions"]
    assert q["result"]["outcome"] == "format_error"
    assert q["result"]["reasons"][0].startswith("agent failed: timeout"), q["result"]["reasons"]


@with_env
def test_agent_gets_question_and_schema_only(env):
    p = env.run("--split", "dev", "--label", "echo", "--questions", "q001,q014", agent="echo")
    ok(p, "echo")
    _, detail = env.latest(p)
    for q in detail["questions"]:
        sent = json.loads(q["response"].removeprefix("payload: "))
        assert set(sent) == {"question", "answer_schema"}, sorted(sent)
        assert sent["question"] == Q[q["id"]]["question"]


@with_env
def test_hidden_requires_confirmation(env):
    for split in ("hidden", "all"):
        p = env.run("--split", split, "--label", "x", agent="perfect")
        assert p.returncode == 2 and "--confirm-hidden" in p.stderr, split
        assert not ID.search(p.stdout + p.stderr)
    assert not env.summaries() and not env.runs.exists()


@with_env
def test_questions_subset_is_dev_only(env):
    hidden_id = next(e["id"] for e in Q.values() if e["split"] == "hidden")
    p = env.run("--split", "dev", "--label", "x", "--questions", f"q001,{hidden_id}", agent="perfect")
    assert p.returncode == 2 and "not dev questions" in p.stderr
    p = env.run("--split", "hidden", "--label", "x", "--questions", "q001", "--confirm-hidden", agent="perfect")
    assert p.returncode == 2 and "dev only" in p.stderr
    assert not env.summaries()


@with_env
def test_dev_console_lists_questions(env):
    p = env.run("--split", "dev", "--label", "perfect", agent="perfect")
    ok(p, "dev")
    dev_ids = {e["id"] for e in Q.values() if e["split"] == "dev"}
    assert set(ID.findall(p.stdout)) == dev_ids
    assert "manual_review: q014" in p.stdout


@with_env
def test_responses_dir(env):
    rdir = env.dir / "responses"
    rdir.mkdir()
    for e in Q.values():
        if e["split"] == "dev":
            (rdir / f"{e['id']}.md").write_text(next(t for _, t in correct_responses(e)))
    p = env.run("--split", "dev", "--label", "files", "--responses-dir", str(rdir))
    ok(p, "responses-dir")
    summary, _ = env.latest(p)
    assert summary["rates"]["dev"]["accuracy"] == 1.0 and summary["agent_cmd"] is None
    (rdir / "q001.md").unlink()
    p = env.run("--split", "dev", "--label", "files", "--responses-dir", str(rdir))
    ok(p, "missing file")
    _, detail = env.latest(p)
    (q,) = [q for q in detail["questions"] if q["id"] == "q001"]
    assert q["result"]["outcome"] == "format_error"
    assert q["result"]["reasons"] == ["agent failed: no response file"]


def test_hidden_values_list_is_not_empty():
    strings, numbers = test_grader.hidden_values()
    assert len(strings) > 100 and len(numbers) > 10  # the leak checks above compare against something


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
