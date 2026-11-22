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
SHOW = HERE / "show_detail.py"
MARKER = "TRACE-MARKER-7f3a"
ID = re.compile(r"\bq\d{3}\b")

AGENTS = {
    "perfect": """
import json, sys
from pathlib import Path
q = json.loads(sys.stdin.read())
print(json.loads((Path(__file__).parent / "responses.json").read_text())[q["question"]])
print('USAGE {"input_tokens": 1000, "output_tokens": 200, "tool_calls": 3}', file=sys.stderr)
""",
    "configured": """
import json, sys
from pathlib import Path
q = json.loads(sys.stdin.read())
print(json.loads((Path(__file__).parent / "responses.json").read_text())[q["question"]])
counter = Path(__file__).parent / "configured.count"
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
config = {"model": "claude-first-x" if n == 0 else "claude-later-x", "docs": "none", "brief": "minimal",
          "scope": "raw"}
print("USAGE " + json.dumps({"input_tokens": 10, "output_tokens": 2, "tool_calls": 1, **config}), file=sys.stderr)
""",
    "three_keys": """
import json, sys
from pathlib import Path
q = json.loads(sys.stdin.read())
print(json.loads((Path(__file__).parent / "responses.json").read_text())[q["question"]])
print('USAGE {"input_tokens": 1, "output_tokens": 1, "tool_calls": 1, "model": "claude-x", "docs": "full", '
      '"brief": "full"}', file=sys.stderr)
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
print('USAGE {"input_tokens": 50, "output_tokens": 5, "tool_calls": 1}', file=sys.stderr)
sys.exit(1)
""",
    "noisy": """
import sys
sys.stdin.read()
print("partial", file=sys.stderr)
print('USAGE {"input_tokens": 1, "output_tokens": 1, "tool_calls": 1}', file=sys.stderr)
print('USAGE not json', file=sys.stderr)
print('USAGE {"input_tokens": 7, "output_tokens": 2, "tool_calls": 4}', file=sys.stderr)
print("no json here")
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
    "tracer": """
import json, os, sys
from pathlib import Path
q = json.loads(sys.stdin.read())
path = os.environ["AGENT_TRACE_FILE"]
with open(path, "a") as f:
    f.write(json.dumps({"event": "sql", "turn": 1, "tool_use_id": "tu_1", "sql": "SELECT 1 -- TRACE-MARKER-7f3a"}) + "\\n")
    f.write(json.dumps({"event": "tool_result", "tool_use_id": "tu_1", "content": path, "is_error": False}) + "\\n")
    f.write("not json TRACE-MARKER-7f3a\\n")
print(json.loads((Path(__file__).parent / "responses.json").read_text())[q["question"]])
""",
    "trace_crasher": """
import json, os, sys
sys.stdin.read()
with open(os.environ["AGENT_TRACE_FILE"], "a") as f:
    f.write(json.dumps({"event": "error", "message": "boom TRACE-MARKER-7f3a"}) + "\\n")
sys.exit(1)
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


@with_env
def test_usage_totals(env):
    n_dev = sum(e["split"] == "dev" for e in Q.values())
    p = env.run("--split", "dev", "--label", "usage", agent="perfect")
    ok(p, "perfect")
    summary, detail = env.latest(p)
    assert summary["usage"] == {"input_tokens": 1000 * n_dev, "output_tokens": 200 * n_dev,
                                "tool_calls": 3 * n_dev}, summary["usage"]
    assert "usage: input_tokens" in p.stdout
    assert all("usage" not in q for q in detail["questions"]), "per-question usage in detail"

    p = env.run("--split", "dev", "--label", "crash", agent="crasher")  # failed calls still count
    ok(p, "crash")
    summary, _ = env.latest(p)
    assert summary["usage"] == {"input_tokens": 50 * n_dev, "output_tokens": 5 * n_dev, "tool_calls": n_dev}

    p = env.run("--split", "dev", "--label", "noisy", "--questions", "q001", agent="noisy")  # last valid line wins
    ok(p, "noisy")
    summary, _ = env.latest(p)
    assert summary["usage"] == {"input_tokens": 7, "output_tokens": 2, "tool_calls": 4}, summary["usage"]

    p = env.run("--split", "dev", "--label", "nousage", agent="refuser")  # no USAGE line: zeros
    ok(p, "refuser")
    summary, _ = env.latest(p)
    assert summary["usage"] == {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}


@with_env
def test_usage_is_totals_only(env):
    p = env.run("--split", "all", "--label", "usage-all", "--confirm-hidden", agent="perfect")
    ok(p, "all")
    summary, _ = env.latest(p)
    assert set(summary["usage"]) == {"input_tokens", "output_tokens", "tool_calls"}
    assert all(type(v) is int for v in summary["usage"].values())
    assert not ID.search(json.dumps(summary)), "summary holds question ids"
    assert not ID.search(p.stdout), "console holds question ids"


@with_env
def test_usage_null_for_responses_dir(env):
    rdir = env.dir / "responses"
    rdir.mkdir()
    p = env.run("--split", "dev", "--label", "files", "--responses-dir", str(rdir))
    ok(p, "responses-dir")
    summary, _ = env.latest(p)
    assert summary["usage"] is None and "usage:" not in p.stdout


@with_env
def test_agent_config_in_summary(env):
    p = env.run("--split", "dev", "--label", "config", agent="configured")
    ok(p, "configured")
    summary, detail = env.latest(p)
    first = {"model": "claude-first-x", "docs": "none", "brief": "minimal", "scope": "raw"}
    assert summary["agent_config"] == first, summary["agent_config"]  # the first question's, later ones ignored
    assert detail["agent_config"] == first
    assert all("agent_config" not in q and "usage" not in q for q in detail["questions"]), "per-question config"
    assert "claude-later-x" not in json.dumps(summary)  # (detail keeps each raw stderr_tail, as before)
    assert "agent_config: model claude-first-x  docs none  brief minimal  scope raw" in p.stdout
    assert summary["usage"]["tool_calls"] == sum(e["split"] == "dev" for e in Q.values())

    p = env.run("--split", "dev", "--label", "noscope", "--questions", "q001", agent="three_keys")  # no scope
    ok(p, "three_keys")
    summary, _ = env.latest(p)
    assert summary["agent_config"] is None, summary["agent_config"]

    p = env.run("--split", "dev", "--label", "noconfig", "--questions", "q001", agent="perfect")  # counts only
    ok(p, "perfect")
    summary, _ = env.latest(p)
    assert summary["agent_config"] is None and "agent_config:" not in p.stdout

    rdir = env.dir / "responses"
    rdir.mkdir()
    p = env.run("--split", "dev", "--label", "files", "--responses-dir", str(rdir))
    ok(p, "responses-dir")
    summary, _ = env.latest(p)
    assert summary["agent_config"] is None


SUMMARY_KEYS = {"timestamp", "split", "label", "git_sha", "git_dirty", "agent_cmd", "responses_dir", "timeout",
                "n_questions", "rates", "usage", "agent_config"}
RATE_KEYS = {"questions", "answerable", "correct", "accuracy", "wrong_confident_rate", "false_refusal_rate",
             "incomplete_refusal_rate", "format_error_rate"}


@with_env
def test_summaries_hold_no_per_question_data(env):
    for split, agent in (("dev", "configured"), ("hidden", "configured"), ("all", "configured"), ("dev", "crasher"),
                         ("all", "perfect")):
        extra = ["--confirm-hidden"] if split != "dev" else []
        ok(env.run("--split", split, "--label", f"{agent}-{split}", *extra, agent=agent), f"{agent} {split}")
    paths = env.summaries()
    assert len(paths) == 5, paths
    for path in paths:
        summary = json.loads(path.read_text())
        expected = SUMMARY_KEYS | ({"earlier_hidden_runs"} if summary["split"] != "dev" else set())
        assert set(summary) == expected, (path.name, set(summary) ^ expected)
        assert not ID.search(path.read_text()), f"{path.name} holds question ids"
        for split, rates in summary["rates"].items():
            assert set(rates) == RATE_KEYS and all(isinstance(v, (int, float)) for v in rates.values()), split
        assert set(summary["usage"]) == {"input_tokens", "output_tokens", "tool_calls"}
        assert all(type(v) is int for v in summary["usage"].values())
        config = summary["agent_config"]
        assert config is None or (set(config) == {"model", "docs", "brief", "scope"}
                                  and all(isinstance(v, str) for v in config.values())), config
        assert_no_leak(summary, path.name)


@with_env
def test_trace_in_detail_only(env):
    p = env.run("--split", "dev", "--label", "trace", agent="tracer")
    ok(p, "tracer")
    summary, detail = env.latest(p)
    paths = []
    for q in detail["questions"]:
        events = q["trace"]
        assert [e["event"] for e in events] == ["sql", "tool_result", "unparsed"], (q["id"], events)
        assert MARKER in events[0]["sql"] and MARKER in events[2]["raw"]
        paths.append(events[1]["content"])
    assert len(set(paths)) == len(paths) == summary["n_questions"], "trace files are not per call"
    assert not any(Path(x).exists() for x in paths), "temp trace files left behind"
    (summary_path,) = env.summaries()
    assert MARKER not in summary_path.read_text(), "trace in summary"
    assert "trace" not in summary
    assert MARKER not in p.stdout + p.stderr, "trace on console"
    assert summary["rates"]["dev"]["accuracy"] == 1.0

    p = env.run("--split", "dev", "--label", "trace-crash", "--questions", "q001", agent="trace_crasher")
    ok(p, "trace_crasher")
    _, detail = env.latest(p)
    (q,) = detail["questions"]
    assert q["result"]["outcome"] == "format_error"
    assert q["trace"] == [{"event": "error", "message": f"boom {MARKER}"}]
    assert MARKER not in p.stdout + p.stderr

    p = env.run("--split", "dev", "--label", "no-trace", "--questions", "q001", agent="perfect")
    ok(p, "perfect")
    assert env.latest(p)[1]["questions"][0]["trace"] == []
    rdir = env.dir / "responses"
    rdir.mkdir()
    p = env.run("--split", "dev", "--label", "files", "--questions", "q001", "--responses-dir", str(rdir))
    ok(p, "responses-dir")
    assert env.latest(p)[1]["questions"][0]["trace"] is None


def show(*args):
    return subprocess.run([sys.executable, str(SHOW), *map(str, args)], capture_output=True, text=True, timeout=60)


@with_env
def test_show_detail_dev(env):
    p = env.run("--split", "dev", "--label", "trace", "--questions", "q001", agent="tracer")
    ok(p, "tracer")
    detail_path = env.runs / "detail" / Path(re.search(r"^summary: (.+)$", p.stdout, re.M).group(1)).name
    s = show(detail_path, "q001")
    assert s.returncode == 0, s.stderr
    out = s.stdout
    for header in ("== question q001", "== trace", "== response", "== outcome"):
        assert header in out, header
    assert Q["q001"]["question"].strip() in out
    assert MARKER in out and "[sql, turn 1, tu_1]" in out and "[unparsed]" in out
    assert "outcome: correct" in out and "reasons:" in out
    s = show(detail_path, "q999")
    assert s.returncode == 2 and s.stdout == ""


@with_env
def test_show_detail_refuses_hidden(env):
    p = env.run("--split", "all", "--label", "all", "--confirm-hidden", agent="tracer")
    ok(p, "all")
    detail_path = env.runs / "detail" / Path(re.search(r"^summary: (.+)$", p.stdout, re.M).group(1)).name
    hidden = [e for e in Q.values() if e["split"] == "hidden"]
    assert hidden
    for e in hidden:
        s = show(detail_path, e["id"])
        assert s.returncode == 2, (e["id"], s.returncode)
        assert s.stdout == "" and "dev only" in s.stderr
        assert e["question"].strip() not in s.stderr
    assert show(detail_path, "q001").returncode == 0


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
