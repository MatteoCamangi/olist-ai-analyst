"""Run the reviewer agent on evals/reviewer/cases.json and score its verdicts.

Usage (from the repo root):
    python evals/reviewer/run_reviewer_eval.py --label baseline
    python evals/reviewer/run_reviewer_eval.py --label quick --only q013.ok,q013.wrong_filter
    python evals/reviewer/run_reviewer_eval.py --label x --agent-cmd "python agent/reviewer.py" --timeout 300

Each case runs in a fresh process. The agent gets only {question, answer_schema, response, trace} on stdin; the
answer key (expected verdict and checks, fault type, origin, edits, ids) stays here. The agent prints its verdict
JSON on stdout and one stderr line  USAGE {"input_tokens": n, "output_tokens": n, "model": s}.

Outputs:
    evals/reviewer/runs/<UTC timestamp>_<label>.json         summary: counts, rates, tables, tokens (committed)
    evals/reviewer/runs/detail/<UTC timestamp>_<label>.json  per-case reviewer outputs and scores (gitignored)
Guard: if every case fails before reaching the model (agent failure and zero tokens), nothing is saved; exit 3.
The metrics, the evidence rule and the invented-number rule are described in evals/reviewer/README.md.
"""
import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "agent"))
import run_eval  # noqa: E402  (git_info)
import reviewer  # noqa: E402  (input fields, query numbering, check names)

CASES = HERE / "cases.json"
RUNS = HERE / "runs"
DEFAULT_CMD = "python agent/reviewer.py"
PAYLOAD_FIELDS = reviewer.INPUT_FIELDS  # the only case fields the agent ever sees
DOC_SOURCES = ("docs/metrics.md", "evals/ANSWER_FORMAT.md")  # also accepted as evidence and number sources
VERDICTS = reviewer.VERDICTS
CHECKS = reviewer.CHECKS
USAGE_LINE = re.compile(r"^USAGE (\{.*\})\s*$", re.MULTILINE)
METRICS = ("verdict_match", "dangerous_miss", "false_alarm", "soft_miss", "check_recall", "evidence_valid",
           "invented_numbers", "format_error", "agent_failure")

_QUERY_REF = re.compile(r"\b(?:quer(?:y|ies)|q)\s*#?\s*(\d+(?:\s*(?:,|and|&|or|-|to)\s*(?:query\s*)?#?\s*\d+)*)",
                        re.IGNORECASE)
_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|`([^`]+)`')
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")


# -- agent call ------------------------------------------------------------------------------------------

def payload(case):
    return json.dumps({k: case[k] for k in PAYLOAD_FIELDS}, ensure_ascii=False)


def parse_usage(stderr):
    for raw in reversed(USAGE_LINE.findall(stderr or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and all(type(data.get(k)) is int and data[k] >= 0
                                          for k in ("input_tokens", "output_tokens")):
            model = data.get("model")
            return {"input_tokens": data["input_tokens"], "output_tokens": data["output_tokens"],
                    "model": model if isinstance(model, str) else None}
    return None


def call_agent(cmd, case, timeout):
    """(stdout, stderr, failure reason or None)."""
    try:
        proc = subprocess.Popen(cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, start_new_session=True, cwd=ROOT)
    except OSError as exc:
        return "", "", f"agent failed: {type(exc).__name__}"
    try:
        out, err = proc.communicate(payload(case), timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        return out or "", err or "", f"agent failed: timeout after {timeout:g}s"
    if proc.returncode != 0:
        return out, err, f"agent failed: exit code {proc.returncode}"
    if not out.strip():
        return out, err, "agent failed: empty output"
    return out, err, None


def classify(stdout):
    """('valid', verdict object) | ('format_error', None) | ('agent_failure', None) from the agent's stdout."""
    try:
        data = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return "agent_failure", None
    if not isinstance(data, dict):
        return "agent_failure", None
    if data.get("verdict") is None:
        return "format_error", None
    findings = data.get("findings")
    ok = (data.get("verdict") in VERDICTS and isinstance(findings, list)
          and all(isinstance(f, dict) and isinstance(f.get("check"), str) for f in findings))
    return ("valid", data) if ok else ("format_error", None)


# -- deterministic evidence and number checks ------------------------------------------------------------

def norm(text):
    return " ".join(str(text).split()).lower()


def case_sources(case, docs):
    """(normalized source texts, query count) for one case: response, every query and result, the docs."""
    qs = reviewer.queries(case["trace"])
    texts = [case["response"]] + [q[1] for q in qs] + [q[2] for q in qs] + list(docs)
    return [norm(t) for t in texts], len(qs)


def query_refs(text):
    refs = []
    for m in _QUERY_REF.finditer(text):
        refs += [int(n) for n in re.findall(r"\d+", m.group(1))]
    return refs


def evidence_ok(evidence, sources, n_queries):
    """The README's evidence rule for one finding."""
    refs = query_refs(evidence)
    if any(not 1 <= n <= n_queries for n in refs):
        return False
    spans = [s for m in _QUOTED.finditer(evidence) for s in m.groups() if s and len(s.strip()) >= 4]
    found = [s for s in spans if any(norm(s) in src for src in sources)]
    if len(found) != len(spans):
        return False
    whole = norm(evidence)
    return bool(refs) or bool(found) or (len(whole) >= 8 and any(whole in src for src in sources))


def numbers_in(text):
    """Numbers as (value, decimals), query references and integers 0-10 left out."""
    text = _QUERY_REF.sub(" ", text)
    out = []
    for raw in _NUMBER.findall(text):
        s = raw.rstrip("%").replace(",", "")
        try:
            value = float(s)
        except ValueError:
            continue
        decimals = len(s.split(".", 1)[1]) if "." in s else 0
        if decimals == 0 and 0 <= abs(value) <= 10:
            continue
        out.append((value, decimals))
    return out


def source_numbers(case, docs):
    qs = reviewer.queries(case["trace"])
    blob = "\n".join([case["response"]] + [q[1] for q in qs] + [q[2] for q in qs] + list(docs))
    values = set()
    for raw in _NUMBER.findall(blob):
        try:
            values.add(float(raw.rstrip("%").replace(",", "")))
        except ValueError:
            pass
    return values


def number_supported(value, decimals, sources):
    return any(abs(s - value) < 1e-9 or abs(round(s, decimals) - value) < 1e-9 for s in sources)


def invented_numbers(findings, numbers):
    bad = []
    for f in findings:
        for value, decimals in numbers_in(f"{f.get('evidence', '')} {f.get('fix', '')}"):
            if not number_supported(value, decimals, numbers):
                bad.append(value)
    return bad


# -- scoring ---------------------------------------------------------------------------------------------

def score_case(case, status, output, docs):
    """Per-case flags. None means the metric does not apply to this case (not in its denominator)."""
    expected = case["expected_verdict"]
    exp_checks = set(case["expected_failed_checks"])
    valid = status == "valid"
    got = output["verdict"] if valid else status
    findings = output["findings"] if valid else []
    flagged = {f["check"] for f in findings}
    problem = expected != "approve"
    s = {"got": got, "flagged_checks": sorted(flagged),
         "verdict_match": valid and got == expected,
         "dangerous_miss": (valid and got == "approve") if problem else None,
         "false_alarm": (valid and got != "approve") if not problem else None,
         "soft_miss": (valid and got in ("revise", "reject") and got != expected) if problem else None,
         "check_recall": (valid and exp_checks <= flagged) if exp_checks else None,
         "evidence_valid": None, "invented_numbers": None,
         "format_error": status == "format_error", "agent_failure": status == "agent_failure"}
    if valid and findings:
        sources, n_queries = case_sources(case, docs)
        s["evidence_valid"] = all(evidence_ok(f.get("evidence", ""), sources, n_queries) for f in findings)
        s["invented_numbers"] = bool(invented_numbers(findings, source_numbers(case, docs)))
    return s


def rate(flags):
    applied = [f for f in flags if f is not None]
    n = sum(1 for f in applied if f)
    return {"n": n, "of": len(applied), "rate": round(n / len(applied), 4) if applied else None}


def group_of(case):
    return case["fault_type"] or f"natural_{case['expected_verdict']}"


def summarize(items):
    scores = [i["scores"] for i in items]
    metrics = {m: rate([s[m] for s in scores]) for m in METRICS}
    per_fault = {}
    for i in items:
        per_fault.setdefault(group_of(i["case"]), []).append(i["scores"])
    per_fault_type = {g: {"n": len(ss), **{m: rate([s[m] for s in ss])
                                           for m in ("verdict_match", "dangerous_miss", "check_recall")}}
                      for g, ss in sorted(per_fault.items())}
    per_check = {}
    for check in CHECKS:
        exp = [i for i in items if check in i["case"]["expected_failed_checks"]]
        per_check[check] = {
            "expected": len(exp),
            "recalled": sum(check in i["scores"]["flagged_checks"] for i in exp),
            "flagged": sum(check in i["scores"]["flagged_checks"] for i in items),
            "flagged_on_approve": sum(check in i["scores"]["flagged_checks"] for i in items
                                      if i["case"]["expected_verdict"] == "approve")}
    columns = list(VERDICTS) + ["format_error", "agent_failure"]
    confusion = {e: {g: sum(i["case"]["expected_verdict"] == e and i["scores"]["got"] == g for i in items)
                     for g in columns} for e in VERDICTS}
    tokens = {k: sum((i["usage"] or {}).get(k, 0) for i in items) for k in ("input_tokens", "output_tokens")}
    return metrics, per_fault_type, per_check, confusion, tokens


# -- run -------------------------------------------------------------------------------------------------

def run_case(case, args, docs):
    start = time.monotonic()
    out, err, failure = call_agent(args.agent_cmd, case, args.timeout)
    status, output = ("agent_failure", None) if failure else classify(out)
    if status == "agent_failure" and not failure:
        failure = "agent failed: output is not a JSON object"
    return {"case": case, "status": status, "output": output, "failure": failure,
            "usage": parse_usage(err), "stderr_tail": (err or "")[-2000:],
            "seconds": round(time.monotonic() - start, 2), "scores": score_case(case, status, output, docs)}


def run_name(runs_dir, label):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "run"
    name, i = f"{stamp}_{slug}", 2
    while (Path(runs_dir) / f"{name}.json").exists():
        name, i = f"{stamp}_{slug}-{i}", i + 1
    return stamp, name


def fmt(r):
    return f"{r['n']}/{r['of']}" if r["of"] else "-"


def print_table(summary):
    print(f"{'metric':<18}{'n/of':>8}{'rate':>8}")
    for m, r in summary["metrics"].items():
        shown = "" if r["rate"] is None else f"{r['rate']:.2f}"
        print(f"{m:<18}{fmt(r):>8}{shown:>8}")
    print()
    print(f"{'group':<26}{'n':>3}{'match':>8}{'danger':>8}{'recall':>8}")
    for g, r in summary["per_fault_type"].items():
        print(f"{g:<26}{r['n']:>3}{fmt(r['verdict_match']):>8}{fmt(r['dangerous_miss']):>8}"
              f"{fmt(r['check_recall']):>8}")
    print()
    print(f"{'check':<26}{'expected':>9}{'recalled':>9}{'flagged':>8}{'on approve':>11}")
    for c, r in summary["per_check"].items():
        print(f"{c:<26}{r['expected']:>9}{r['recalled']:>9}{r['flagged']:>8}{r['flagged_on_approve']:>11}")
    t = summary["tokens"]
    print(f"\ntokens: input {t['input_tokens']}  output {t['output_tokens']}  "
          f"model {(summary['agent_config'] or {}).get('model')}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Run and score the reviewer agent on the reviewer cases.")
    p.add_argument("--label", required=True)
    p.add_argument("--cases", type=Path, default=CASES)
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--agent-cmd", default=DEFAULT_CMD)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--runs-dir", type=Path, default=RUNS, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    cases = json.loads(args.cases.read_text())
    if args.only:
        ids = [x.strip() for x in args.only.split(",") if x.strip()]
        by_id = {c["id"]: c for c in cases}
        unknown = [x for x in ids if x not in by_id]
        if unknown:
            p.error(f"unknown case ids: {', '.join(unknown)}")
        cases = [by_id[x] for x in ids]
    docs = [(ROOT / d).read_text() for d in DOC_SOURCES]
    sha, dirty = run_eval.git_info()

    items = [run_case(case, args, docs) for case in cases]
    tokens_used = sum((i["usage"] or {}).get(k, 0) for i in items for k in ("input_tokens", "output_tokens"))
    if items and all(i["status"] == "agent_failure" for i in items) and tokens_used == 0:
        reasons = sorted({i["failure"] for i in items})
        print("WARNING: every case failed before reaching the model; summary not saved", file=sys.stderr)
        print(f"failures: {'; '.join(reasons)}", file=sys.stderr)
        return 3

    metrics, per_fault_type, per_check, confusion, tokens = summarize(items)
    first_usage = items[0]["usage"] if items else None
    stamp, name = run_name(args.runs_dir, args.label)
    summary = {"timestamp": stamp, "label": args.label, "git_sha": sha, "git_dirty": dirty,
               "agent_cmd": args.agent_cmd, "cases_file": str(args.cases), "n_cases": len(items),
               "only": [c["id"] for c in cases] if args.only else None,
               "agent_config": {"model": first_usage["model"]} if first_usage else None,
               "metrics": metrics, "per_fault_type": per_fault_type, "per_check": per_check,
               "confusion": confusion, "tokens": tokens}
    detail = {**summary, "cases": [
        {"id": i["case"]["id"], "fault_type": i["case"]["fault_type"],
         "expected_verdict": i["case"]["expected_verdict"],
         "expected_failed_checks": i["case"]["expected_failed_checks"], "status": i["status"],
         "failure": i["failure"], "output": i["output"], "scores": i["scores"], "usage": i["usage"],
         "seconds": i["seconds"], "stderr_tail": i["stderr_tail"]} for i in items]}
    (args.runs_dir / "detail").mkdir(parents=True, exist_ok=True)
    summary_path, detail_path = args.runs_dir / f"{name}.json", args.runs_dir / "detail" / f"{name}.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    detail_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False) + "\n")
    print_table(summary)
    print(f"summary: {summary_path}\ndetail:  {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
