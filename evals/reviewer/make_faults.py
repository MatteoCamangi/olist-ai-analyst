"""Build evals/reviewer/cases.json: dev-only test cases for the reviewer agent.

Usage (from the repo root, with the venv active):
    python evals/reviewer/make_faults.py           # rebuild cases.json and print the case table
    python evals/reviewer/make_faults.py --check   # rebuild in memory, exit 1 if cases.json differs

Cases come from local eval detail files (evals/runs/detail/ is gitignored), dev runs only:
- natural, approve: the answers of the full-context dev run GOOD_RUN, copied untouched, except EXCLUDED (their traces
  hold values that are only in hidden expected answers).
- natural, reject: NATURAL, a real wrong answer.
- injected: a copy of one approve case with deterministic string edits to its response and trace. Verdicts follow
  agent/REVIEWER_BRIEF.md: reject when the headline answer becomes wrong, revise when it stays right.
Check names are the checks of agent/REVIEWER_BRIEF.md section 3.

No model is called and no agent runs. Edited SQL is re-run read-only on warehouse.duckdb, and the build stops if its
real result differs from the result written into the faulty trace. Only files named *_dev_* are opened, and every
item used must be a dev question both in the detail file and in questions.yaml.
"""
import argparse
import datetime as dt
import decimal
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
import grader  # noqa: E402

DETAIL_DIR = ROOT / "evals" / "runs" / "detail"
CASES = HERE / "cases.json"
DB_PATH = ROOT / "warehouse.duckdb"
GOOD_RUN = "20261121T174710Z_dev_v3-harder"
EXCLUDED = ("q007", "q011")  # their traces contain ids that appear only in hidden expected answers
NATURAL = {"run": "20261122T170925Z_dev_grid-raw-bare", "question": "q010", "id": "q010.raw-bare",
           "verdict": "reject", "checks": ["definitions"],
           "note": "Compares delivery and estimate timestamps instead of calendar dates, so the late rate is "
                   "overstated (AL 23.93% instead of 21.41%)."}
CHECKS = ("numbers", "definitions", "grain_population", "assumptions", "refusals_false_premises",
          "claims_beyond_data", "format")  # agent/REVIEWER_BRIEF.md section 3
VERDICTS = ("approve", "revise", "reject")
FAULT_TYPES = ("wrong_filter", "threshold_off_by_one", "unsupported_claim", "invented_cause", "missing_assumption",
               "contradicting_assumption")
SCOPES = ("sql", "result", "text")  # text = the response and the trace's text and final events

FAULTS = [
    {
        "type": "wrong_filter", "base": "q013", "verdict": "reject", "checks": ["definitions"],
        "note": "Counts unavailable orders as canceled, while the response's own assumption says they are not.",
        "edits": [
            {"scope": "sql", "old": "order_status='canceled'", "new": "order_status IN ('canceled','unavailable')",
             "count": 2},
            {"scope": "result", "old": "99441\t625\t0.63\t", "new": "99441\t1234\t1.24\t", "count": 1},
            {"scope": "text", "old": "625 canceled orders", "new": "1,234 canceled orders", "count": 1},
            {"scope": "text", "old": "0.63%", "new": "1.24%", "count": 1},
            {"scope": "text", "old": '"canceled_orders_cnt": 625', "new": '"canceled_orders_cnt": 1234', "count": 1},
            {"scope": "text", "old": '"cancel_order_rate_pct": 0.63', "new": '"cancel_order_rate_pct": 1.24',
             "count": 1},
        ],
    },
    {
        "type": "threshold_off_by_one", "base": "q001", "verdict": "reject", "checks": ["grain_population"],
        "note": "The year filter excludes 2017-12-31 (< instead of <=), while the assumptions still say the year "
                "runs to 2017-12-31.",
        "edits": [
            {"scope": "sql", "old": "order_purchase_day BETWEEN DATE '2017-01-01' AND DATE '2017-12-31'",
             "new": "order_purchase_day >= DATE '2017-01-01' AND order_purchase_day < DATE '2017-12-31'",
             "count": 1},
            {"scope": "result", "old": "45101\t43428\t5962902.01\t2017-01-05\t2017-12-31",
             "new": "45027\t43357\t5954439.13\t2017-01-05\t2017-12-30", "count": 1},
            {"scope": "text", "old": "45,101", "new": "45,027", "count": 1},
            {"scope": "text", "old": "43,428", "new": "43,357", "count": 1},
            {"scope": "text", "old": "5,962,902.01", "new": "5,954,439.13", "count": 1},
            {"scope": "text", "old": '"orders_cnt": 45101', "new": '"orders_cnt": 45027', "count": 1},
            {"scope": "text", "old": '"delivered_orders_cnt": 43428', "new": '"delivered_orders_cnt": 43357',
             "count": 1},
            {"scope": "text", "old": '"total_revenue": 5962902.01', "new": '"total_revenue": 5954439.13',
             "count": 1},
        ],
    },
    {
        "type": "unsupported_claim", "base": "q019", "verdict": "revise", "checks": ["claims_beyond_data"],
        "note": "Adds a share of 5-star reviews that no query computed; the headline average is still right.",
        "edits": [
            {"scope": "text", "old": "It covers 95,832 delivered orders that have a review.",
             "new": "It covers 95,832 delivered orders that have a review. More than 60% of these orders gave the "
                    "top score of 5, so the average rests on a large block of very satisfied customers.",
             "count": 1},
        ],
    },
    {
        "type": "invented_cause", "base": "q010", "verdict": "revise", "checks": ["claims_beyond_data"],
        "note": "Replaces the association caveat with a cause (seller location, transit) that no query looked at; "
                "the headline rate is still right.",
        "edits": [
            {"scope": "text", "old": "This is an association. The data doesn't show why AL is late more often.",
             "new": "AL is late more often because most of its orders ship from sellers in São Paulo, and the "
                    "estimate does not allow for the long road transit to the Northeast.",
             "count": 1},
        ],
    },
    {
        "type": "missing_assumption", "base": "q018", "verdict": "revise", "checks": ["assumptions"],
        "note": "'Performs best' is ambiguous, but the response no longer says it ranked by revenue; the number stays.",
        "edits": [
            {"scope": "text",
             "old": "\n\n\"Performs best\" can be read several ways, so I picked revenue. Ranked by volume, "
                    "bed_bath_table would lead with 10,953 items against 9,465 for health_beauty. This is a ranking, "
                    "not a cause. Revenue is gross, with no refunds or costs, so it says nothing about profit.",
             "new": "", "count": 1},
            {"scope": "text",
             "old": "\"assumptions\": [\n"
                    "    \"'Performs best' is ambiguous; defined as highest revenue (sum of item price, freight "
                    "excluded)\",\n"
                    "    \"Delivered orders only; categories are English names from marts.fct_order_items, with "
                    "'unknown' kept in the total\",\n"
                    "    \"Revenue is gross: no refund, cost or commission data, so this is not profit\",\n"
                    "    \"By item volume, bed_bath_table would lead (10,953 items vs 9,465 for health_beauty)\",\n"
                    "    \"Share of total delivered revenue (13,221,498.11) is 9.33%\",\n"
                    "    \"Data covers 2016-09-04 to 2018-10-17; no date filter applied. 2016 and Aug-Oct 2018 are "
                    "sparse or partial\"\n"
                    "  ],",
             "new": "\"assumptions\": [],", "count": 1},
        ],
    },
    {
        "type": "contradicting_assumption", "base": "q020", "verdict": "revise", "checks": ["assumptions"],
        "note": "States that revenue includes freight and all statuses, which contradicts docs/metrics.md; the SQL "
                "and the number are unchanged.",
        "edits": [
            {"scope": "text",
             "old": "\"Revenue is the sum of item price (freight excluded) on delivered orders only "
                    "(order_status = 'delivered')\"",
             "new": "\"Revenue is the sum of item price plus freight on all orders, whatever their status\"",
             "count": 1},
        ],
    },
]


# -- sources ---------------------------------------------------------------------------------------------

def dev_questions():
    """question and answer_schema of the dev entries only (the two fields the agent sees)."""
    return {qid: {"question": e["question"], "answer_schema": e["answer_schema"]}
            for qid, e in grader.load_questions().items() if e["split"] == "dev"}


def load_dev_run(run, dev_ids):
    if "_dev_" not in run:
        raise SystemExit(f"make_faults: {run} is not a dev run")
    path = DETAIL_DIR / f"{run}.json"
    if not path.is_file():
        raise SystemExit(f"make_faults: missing local detail file {path.relative_to(ROOT)}")
    data = json.loads(path.read_text())
    if data.get("split") != "dev":
        raise SystemExit(f"make_faults: {run} is not a dev run")
    items = {}
    for item in data["questions"]:
        if item["id"] not in dev_ids or (item.get("result") or {}).get("split") != "dev":
            raise SystemExit(f"make_faults: {run} holds a non-dev item")
        items[item["id"]] = item
    return data, items


def content_hash(response, trace):
    blob = json.dumps({"response": response, "trace": trace}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def source_of(run, data, item):
    return {"run": run, "git_sha": data.get("git_sha"), "agent_config": data.get("agent_config"),
            "outcome": item["result"]["outcome"], "sha256": content_hash(item["response"], item["trace"])}


def natural_case(case_id, qid, questions, run, data, item, verdict, checks, note):
    return {"id": case_id, "source_question": qid, "split": "dev", **questions[qid],
            "response": item["response"], "trace": item["trace"], "origin": "natural", "fault_type": None,
            "base_case": None, "edits": None, "expected_verdict": verdict, "expected_failed_checks": checks,
            "source": source_of(run, data, item), "note": note}


# -- faults ----------------------------------------------------------------------------------------------

def _replace(text, old, new, count, where):
    n = text.count(old)
    if n != count:
        raise SystemExit(f"make_faults: {where}: expected {count} match(es) of {old[:60]!r}, found {n}")
    return text.replace(old, new)


def apply_edits(response, trace, edits, label):
    """A faulty copy of (response, trace). text edits hit the response (exactly count times) and every trace
    text/final event (count or 0 times each, the final event exactly count times)."""
    response, trace = response, json.loads(json.dumps(trace))
    for edit in edits:
        scope, old, new, count = edit["scope"], edit["old"], edit["new"], edit["count"]
        if scope not in SCOPES:
            raise SystemExit(f"make_faults: {label}: unknown scope {scope}")
        if scope == "text":
            response = _replace(response, old, new, count, f"{label} response")
        for e in trace:
            kind = e.get("event")
            if scope == "sql" and kind == "sql":
                e["sql"] = _replace(e["sql"], old, new, count, f"{label} sql")
            elif scope == "result" and kind == "tool_result":
                e["content"] = _replace(e["content"], old, new, count, f"{label} result")
            elif scope == "text" and kind in ("text", "final"):
                n = e["text"].count(old)
                if n not in ((count,) if kind == "final" else (0, count)):
                    raise SystemExit(f"make_faults: {label} trace {kind}: {n} match(es) of {old[:60]!r}")
                e["text"] = e["text"].replace(old, new)
    return response, trace


def _cell(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return format(v, "f")
    return "NULL" if v is None else str(v)


def verify_sql(con, trace, label):
    """Each SQL in the trace, re-run read-only, must return the data rows written in its tool_result."""
    results = {e["tool_use_id"]: e["content"] for e in trace if e.get("event") == "tool_result"}
    for e in trace:
        if e.get("event") != "sql":
            continue
        rows = con.execute(e["sql"]).fetchall()
        got = ["\t".join(_cell(v) for v in r) for r in rows]
        lines = results[e["tool_use_id"]].splitlines()
        if got != lines[1:1 + len(got)]:
            raise SystemExit(f"make_faults: {label}: the SQL in the trace does not return its recorded result")


def fault_case(spec, base_case, con):
    case_id = f"{spec['base']}.{spec['type']}"
    if spec["verdict"] not in VERDICTS or not set(spec["checks"]) <= set(CHECKS):
        raise SystemExit(f"make_faults: {case_id}: unknown verdict or check")
    response, trace = apply_edits(base_case["response"], base_case["trace"], spec["edits"], case_id)
    if any(edit["scope"] == "sql" for edit in spec["edits"]):
        verify_sql(con, base_case["trace"], f"{case_id} (original)")
        verify_sql(con, trace, case_id)
    return {**base_case, "id": case_id, "response": response, "trace": trace, "origin": "injected",
            "fault_type": spec["type"], "base_case": base_case["id"], "edits": spec["edits"],
            "expected_verdict": spec["verdict"], "expected_failed_checks": spec["checks"], "note": spec["note"]}


# -- build -----------------------------------------------------------------------------------------------

def build():
    import duckdb
    questions = dev_questions()
    data, items = load_dev_run(GOOD_RUN, questions)
    cases = {}
    for qid, item in sorted(items.items()):
        if qid in EXCLUDED:
            continue
        if item["result"]["outcome"] != "correct":
            raise SystemExit(f"make_faults: {GOOD_RUN} {qid} is not correct")
        cases[f"{qid}.ok"] = natural_case(f"{qid}.ok", qid, questions, GOOD_RUN, data, item, "approve", [],
                                          "Correct answer from the full-context dev run.")
    ndata, nitems = load_dev_run(NATURAL["run"], questions)
    nitem = nitems[NATURAL["question"]]
    cases[NATURAL["id"]] = natural_case(NATURAL["id"], NATURAL["question"], questions, NATURAL["run"], ndata,
                                        nitem, NATURAL["verdict"], NATURAL["checks"], NATURAL["note"])
    con = duckdb.connect(str(DB_PATH), read_only=True, config={"enable_external_access": False})
    try:
        con.execute("USE marts")
        for spec in FAULTS:
            case = fault_case(spec, cases[f"{spec['base']}.ok"], con)
            cases[case["id"]] = case
    finally:
        con.close()
    return [cases[k] for k in sorted(cases)]


def render(cases):
    return json.dumps(cases, indent=2, ensure_ascii=False) + "\n"


def table(cases):
    rows = [("case id", "source question", "origin", "fault type", "expected verdict", "failed checks")]
    rows += [(c["id"], c["source_question"], c["origin"], c["fault_type"] or "-", c["expected_verdict"],
              ", ".join(c["expected_failed_checks"]) or "-") for c in cases]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip() for r in rows)


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the reviewer's dev-only test cases.")
    p.add_argument("--check", action="store_true", help="exit 1 if cases.json differs from a fresh build")
    args = p.parse_args(argv)
    cases = build()
    text = render(cases)
    if args.check:
        same = CASES.is_file() and CASES.read_text() == text
        print("cases.json is up to date" if same else "cases.json differs from a fresh build")
        return 0 if same else 1
    CASES.write_text(text)
    print(table(cases))
    print(f"\n{len(cases)} cases written to {CASES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
