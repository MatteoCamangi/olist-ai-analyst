"""Run the reviewer agent on evals/reviewer/cases.json and score its verdicts.

Usage (from the repo root):
    python evals/reviewer/run_reviewer_eval.py --label baseline
    python evals/reviewer/run_reviewer_eval.py --label quick --only q013.ok,q013.wrong_filter
    python evals/reviewer/run_reviewer_eval.py --label x --agent-cmd "python agent/reviewer.py" --timeout 300
    python evals/reviewer/run_reviewer_eval.py --rescore evals/reviewer/runs/detail/<run>.json

Each case runs in a fresh process. The agent gets only {question, answer_schema, response, trace} on stdin; the
answer key (expected verdict and checks, fault type, origin, edits, ids) stays here. The agent prints its verdict
JSON on stdout and one stderr line  USAGE {"input_tokens": n, "output_tokens": n, "model": s}.

Outputs:
    evals/reviewer/runs/<UTC timestamp>_<label>.json         summary: counts, rates, tables, tokens (committed)
    evals/reviewer/runs/detail/<UTC timestamp>_<label>.json  per-case reviewer outputs and scores (gitignored)
Guard: if every case fails before reaching the model (agent failure and zero tokens), nothing is saved; exit 3.
--rescore DETAIL re-scores the reviewer outputs saved in a detail file with the current scorer and the current labels
(no agent call) and writes a new summary and detail labelled <label>-rescored; the originals are never touched.
Metrics are reported under the corrected labels (headline) and under the original labels of corrected cases.
The metrics, the evidence rule and the number rules (scorer version SCORER_VERSION) are in evals/reviewer/README.md.
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
import unicodedata
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
SCORER_VERSION = 2
DOC_SOURCES = ("docs/metrics.md", "evals/ANSWER_FORMAT.md")  # evidence text sources
NUMBER_DOC_SOURCES = ("docs/metrics.md",)  # ANSWER_FORMAT.md example values are illustrative, never number sources
VERDICTS = reviewer.VERDICTS
CHECKS = reviewer.CHECKS
USAGE_LINE = re.compile(r"^USAGE (\{.*\})\s*$", re.MULTILINE)
METRICS = ("verdict_match", "dangerous_miss", "false_alarm", "soft_miss", "check_recall", "evidence_valid",
           "invented_numbers", "derived_numbers", "format_error", "agent_failure")

_QUERY_REF = re.compile(r"\b(?:quer(?:y|ies)|q)\s*#?\s*(\d+(?:\s*(?:,|and|&|or|-|to)\s*(?:query\s*)?#?\s*\d+)*)",
                        re.IGNORECASE)
_DOUBLE_QUOTED = re.compile(r'"([^"]+)"|“([^”]+)”|`([^`]+)`')
# a single quote opens only after start, a space, ':', '(' or '[' and closes only before a space, punctuation or end,
# so apostrophes inside words ("Olist's") never open a span
_SINGLE_QUOTED = re.compile(r"(?:^|(?<=[\s:(\[]))'([^\n]+?)'(?=[\s.,;:!?)\]]|$)")
_ELLIPSIS = re.compile(r"\[\.\.\.\]|\.\.\.|…")
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
    """Both sides of every comparison: accents, curly apostrophes, double quotes, backticks and markdown removed."""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    for ch in ('"', "\u201c", "\u201d", "`", "**", "__"):
        t = t.replace(ch, "")
    return " ".join(t.split()).lower()


def load_docs():
    return {path: (ROOT / path).read_text() for path in DOC_SOURCES}


def case_sources(case, docs):
    """(normalized source texts, query count) for one case: response, every query and result, the docs."""
    qs = reviewer.queries(case["trace"])
    texts = [case["response"]] + [q[1] for q in qs] + [q[2] for q in qs] + [docs[p] for p in DOC_SOURCES]
    return [norm(t) for t in texts], len(qs)


def query_refs(text):
    refs = []
    for m in _QUERY_REF.finditer(text):
        refs += [int(n) for n in re.findall(r"\d+", m.group(1))]
    return refs


def quoted_spans(evidence):
    """Spans in double quotes, curly double quotes, backticks, or boundary-delimited single quotes."""
    text = evidence.replace("\u2018", "'").replace("\u2019", "'")
    spans = [s for m in _DOUBLE_QUOTED.finditer(text) for s in m.groups() if s]
    spans += _SINGLE_QUOTED.findall(_DOUBLE_QUOTED.sub(" ", text))
    return spans


def span_pieces(span):
    """The normalized pieces of a span between ellipses ("...", "…", "[...]"), 4 or more characters each."""
    pieces = (p.strip(" \t.,;:!?") for p in _ELLIPSIS.split(norm(span)))
    return [p for p in pieces if len(p) >= 4]


def evidence_ok(evidence, sources, n_queries):
    """The README's evidence rule for one finding (scorer version 2)."""
    refs = query_refs(evidence)
    if any(not 1 <= n <= n_queries for n in refs):
        return False
    found = 0
    for span in quoted_spans(evidence):
        pieces = span_pieces(span)
        if not pieces:
            continue
        if not any(all(p in src for p in pieces) for src in sources):  # every piece in the same source
            return False
        found += 1
    whole = norm(evidence)
    return bool(refs) or found > 0 or (len(whole) >= 8 and any(whole in src for src in sources))


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


def _numbers(blob):
    values = set()
    for raw in _NUMBER.findall(blob):
        try:
            values.add(float(raw.rstrip("%").replace(",", "")))
        except ValueError:
            pass
    return values


def local_numbers(case):
    """Numbers in the response and the trace's queries and results."""
    qs = reviewer.queries(case["trace"])
    return _numbers("\n".join([case["response"]] + [q[1] for q in qs] + [q[2] for q in qs]))


def source_numbers(case, docs):
    """Numbers a finding may use: the response, the trace and docs/metrics.md (not the ANSWER_FORMAT examples)."""
    return local_numbers(case) | _numbers("\n".join(docs[p] for p in NUMBER_DOC_SOURCES))


def _same(a, value, decimals):
    return abs(a - value) < 1e-9 or abs(round(a, decimals) - value) < 1e-9


def number_supported(value, decimals, sources):
    return any(_same(s, value, decimals) for s in sources)


def number_derived(value, decimals, local):
    """True if value is the sum or difference of two numbers from the response and trace."""
    nums = sorted(local)
    for i, a in enumerate(nums):
        for b in nums[i + 1:]:
            if _same(a + b, value, decimals) or _same(b - a, value, decimals) or _same(a - b, value, decimals):
                return True
    return False


def classify_numbers(findings, case, docs):
    """(invented, derived) numbers over every finding's evidence and fix."""
    sources, local = source_numbers(case, docs), local_numbers(case)
    invented, derived = [], []
    for f in findings:
        for value, decimals in numbers_in(f"{f.get('evidence', '')} {f.get('fix', '')}"):
            if number_supported(value, decimals, sources):
                continue
            (derived if number_derived(value, decimals, local) else invented).append(value)
    return invented, derived


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
         "evidence_valid": None, "invented_numbers": None, "derived_numbers": None,
         "format_error": status == "format_error", "agent_failure": status == "agent_failure"}
    if valid and findings:
        sources, n_queries = case_sources(case, docs)
        s["evidence_valid"] = all(evidence_ok(f.get("evidence", ""), sources, n_queries) for f in findings)
        invented, derived = classify_numbers(findings, case, docs)
        s["invented_numbers"], s["derived_numbers"] = bool(invented), bool(derived)
    return s


def rate(flags):
    applied = [f for f in flags if f is not None]
    n = sum(1 for f in applied if f)
    return {"n": n, "of": len(applied), "rate": round(n / len(applied), 4) if applied else None}


def group_of(case):
    return case["fault_type"] or f"natural_{case['expected_verdict']}"


def original_labels(case):
    """The case as it was labelled before any recorded correction (label_history), else the case itself."""
    history = case.get("label_history")
    if not history:
        return case
    return {**case, **history[0]["from"]}


def summarize(items, case_key="case", score_key="scores"):
    """Tables for one label view: metrics, per fault type, per check, confusion."""
    scores = [i[score_key] for i in items]
    metrics = {m: rate([s[m] for s in scores]) for m in METRICS}
    per_fault = {}
    for i in items:
        per_fault.setdefault(group_of(i[case_key]), []).append(i[score_key])
    per_fault_type = {g: {"n": len(ss), **{m: rate([s[m] for s in ss])
                                           for m in ("verdict_match", "dangerous_miss", "check_recall")}}
                      for g, ss in sorted(per_fault.items())}
    per_check = {}
    for check in CHECKS:
        exp = [i for i in items if check in i[case_key]["expected_failed_checks"]]
        per_check[check] = {
            "expected": len(exp),
            "recalled": sum(check in i[score_key]["flagged_checks"] for i in exp),
            "flagged": sum(check in i[score_key]["flagged_checks"] for i in items),
            "flagged_on_approve": sum(check in i[score_key]["flagged_checks"] for i in items
                                      if i[case_key]["expected_verdict"] == "approve")}
    columns = list(VERDICTS) + ["format_error", "agent_failure"]
    confusion = {e: {g: sum(i[case_key]["expected_verdict"] == e and i[score_key]["got"] == g for i in items)
                     for g in columns} for e in VERDICTS}
    return {"metrics": metrics, "per_fault_type": per_fault_type, "per_check": per_check, "confusion": confusion}


# -- run -------------------------------------------------------------------------------------------------

def make_item(case, status, output, failure, usage, stderr_tail, seconds, docs):
    original = original_labels(case)
    return {"case": case, "case_original": original, "status": status, "output": output, "failure": failure,
            "usage": usage, "stderr_tail": stderr_tail, "seconds": seconds,
            "scores": score_case(case, status, output, docs),
            "scores_original": score_case(original, status, output, docs)}


def run_case(case, args, docs):
    start = time.monotonic()
    out, err, failure = call_agent(args.agent_cmd, case, args.timeout)
    status, output = ("agent_failure", None) if failure else classify(out)
    if status == "agent_failure" and not failure:
        failure = "agent failed: output is not a JSON object"
    return make_item(case, status, output, failure, parse_usage(err), (err or "")[-2000:],
                     round(time.monotonic() - start, 2), docs)


def run_name(runs_dir, label):
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "run"
    name, i = f"{stamp}_{slug}", 2
    while (Path(runs_dir) / f"{name}.json").exists() or (Path(runs_dir) / "detail" / f"{name}.json").exists():
        name, i = f"{stamp}_{slug}-{i}", i + 1
    return stamp, name


def write_run(items, runs_dir, label, meta):
    """Write the summary and detail of a run (or a rescore); never overwrites an existing file."""
    corrected = summarize(items)
    original = summarize(items, "case_original", "scores_original")
    tokens = {k: sum((i["usage"] or {}).get(k, 0) for i in items) for k in ("input_tokens", "output_tokens")}
    stamp, name = run_name(runs_dir, label)
    summary = {"timestamp": stamp, "label": label, "scorer_version": SCORER_VERSION, **meta,
               "n_cases": len(items), "label_view": "corrected",
               "label_corrections": [i["case"]["id"] for i in items if i["case"].get("label_history")],
               **corrected, "original_labels": original, "tokens": tokens}
    detail = {**summary, "cases": [
        {"id": i["case"]["id"], "fault_type": i["case"]["fault_type"],
         "expected_verdict": i["case"]["expected_verdict"],
         "expected_failed_checks": i["case"]["expected_failed_checks"],
         "original_expected_verdict": i["case_original"]["expected_verdict"], "status": i["status"],
         "failure": i["failure"], "output": i["output"], "scores": i["scores"],
         "scores_original": i["scores_original"], "usage": i["usage"], "seconds": i["seconds"],
         "stderr_tail": i["stderr_tail"]} for i in items]}
    (runs_dir / "detail").mkdir(parents=True, exist_ok=True)
    summary_path, detail_path = runs_dir / f"{name}.json", runs_dir / "detail" / f"{name}.json"
    for path, data, ascii_only in ((summary_path, summary, True), (detail_path, detail, False)):
        with open(path, "x") as f:  # "x": fail rather than overwrite
            f.write(json.dumps(data, indent=2, ensure_ascii=ascii_only) + "\n")
    return summary, summary_path, detail_path


def fmt(r):
    return f"{r['n']}/{r['of']}" if r["of"] else "-"


def print_table(summary):
    orig = summary["original_labels"]["metrics"]
    print(f"{'metric':<18}{'n/of':>8}{'rate':>8}{'original labels':>18}")
    for m, r in summary["metrics"].items():
        shown = "" if r["rate"] is None else f"{r['rate']:.2f}"
        print(f"{m:<18}{fmt(r):>8}{shown:>8}{fmt(orig[m]):>18}")
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
          f"model {(summary['agent_config'] or {}).get('model')}  scorer v{summary['scorer_version']}  "
          f"label corrections: {', '.join(summary['label_corrections']) or 'none'}")


def rescore(detail_path, cases_path, runs_dir):
    """Re-score the outputs saved in a detail file with the current scorer and labels. No agent call."""
    detail = json.loads(Path(detail_path).read_text())
    if not isinstance(detail.get("cases"), list) or "label" not in detail:
        raise SystemExit(f"run_reviewer_eval: {detail_path} is not a detail file")
    current = {c["id"]: c for c in json.loads(Path(cases_path).read_text())}
    unknown = [e["id"] for e in detail["cases"] if e["id"] not in current]
    if unknown:
        raise SystemExit(f"run_reviewer_eval: cases not in {cases_path}: {', '.join(unknown)}")
    docs = load_docs()
    items = [make_item(current[e["id"]], e["status"], e["output"], e.get("failure"), e.get("usage"),
                       e.get("stderr_tail", ""), e.get("seconds"), docs) for e in detail["cases"]]
    sha, dirty = run_eval.git_info()
    meta = {"git_sha": sha, "git_dirty": dirty, "agent_cmd": detail.get("agent_cmd"), "cases_file": str(cases_path),
            "only": detail.get("only"), "agent_config": detail.get("agent_config"),
            "rescored_from": Path(detail_path).name, "original_timestamp": detail.get("timestamp"),
            "original_git_sha": detail.get("git_sha")}
    return write_run(items, Path(runs_dir), f"{detail['label']}-rescored", meta)


def main(argv=None):
    p = argparse.ArgumentParser(description="Run and score the reviewer agent on the reviewer cases.")
    p.add_argument("--label")
    p.add_argument("--cases", type=Path, default=CASES)
    p.add_argument("--only", help="comma-separated case ids")
    p.add_argument("--agent-cmd", default=DEFAULT_CMD)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--rescore", type=Path, metavar="DETAIL", help="re-score a detail file; no agent calls")
    p.add_argument("--runs-dir", type=Path, default=RUNS, help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    if args.rescore:
        summary, summary_path, detail_path = rescore(args.rescore, args.cases, args.runs_dir)
        print_table(summary)
        print(f"rescored from: {args.rescore}\nsummary: {summary_path}\ndetail:  {detail_path}")
        return 0
    if not args.label:
        p.error("--label is required (unless --rescore is given)")

    cases = json.loads(args.cases.read_text())
    if args.only:
        ids = [x.strip() for x in args.only.split(",") if x.strip()]
        by_id = {c["id"]: c for c in cases}
        unknown = [x for x in ids if x not in by_id]
        if unknown:
            p.error(f"unknown case ids: {', '.join(unknown)}")
        cases = [by_id[x] for x in ids]
    docs = load_docs()
    sha, dirty = run_eval.git_info()

    items = [run_case(case, args, docs) for case in cases]
    tokens_used = sum((i["usage"] or {}).get(k, 0) for i in items for k in ("input_tokens", "output_tokens"))
    if items and all(i["status"] == "agent_failure" for i in items) and tokens_used == 0:
        reasons = sorted({i["failure"] for i in items})
        print("WARNING: every case failed before reaching the model; summary not saved", file=sys.stderr)
        print(f"failures: {'; '.join(reasons)}", file=sys.stderr)
        return 3

    first_usage = items[0]["usage"] if items else None
    meta = {"git_sha": sha, "git_dirty": dirty, "agent_cmd": args.agent_cmd, "cases_file": str(args.cases),
            "only": [c["id"] for c in cases] if args.only else None,
            "agent_config": {"model": first_usage["model"]} if first_usage else None,
            "rescored_from": None, "original_timestamp": None, "original_git_sha": None}
    summary, summary_path, detail_path = write_run(items, args.runs_dir, args.label, meta)
    print_table(summary)
    print(f"summary: {summary_path}\ndetail:  {detail_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
