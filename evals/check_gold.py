"""Rerun every gold query and compare its result with evals/questions.yaml.

Usage (from the repo root):  python evals/check_gold.py [q001 q014 ...]
Exit code 0 when every checked question matches, 1 on any difference or error.
The database is opened read-only.
"""
import datetime as dt
import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "warehouse.duckdb"
QUESTIONS = ROOT / "evals" / "questions.yaml"
GOLD = ROOT / "evals" / "gold"


def norm(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


def run(con, entry):
    cur = con.execute((ROOT / entry["gold_sql"]).read_text())
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, map(norm, r))) for r in cur.fetchall()]


def same(exp, act, tol):
    if isinstance(exp, (int, float)) and not isinstance(exp, bool) and isinstance(act, (int, float)):
        return abs(exp - act) <= (tol or 0) + 1e-9
    return exp == act


def cmp_values(label, expected, actual, tolerance, errors):
    for k, exp in expected.items():
        if k not in actual:
            errors.append(f"{label}: column {k} missing from the gold result")
        elif not same(exp, actual[k], tolerance.get(k, 0)):
            errors.append(f"{label}.{k}: expected {exp!r}, got {actual[k]!r}")


def cmp_rows(label, expected_rows, actual_rows, tolerance, errors):
    if len(expected_rows) != len(actual_rows):
        errors.append(f"{label}: expected {len(expected_rows)} rows, got {len(actual_rows)}")
    for i, (e, a) in enumerate(zip(expected_rows, actual_rows), start=1):
        cmp_values(f"{label}[{i}]", e, a, tolerance, errors)


def check(con, entry):
    errors = []
    folder = Path(entry["gold_sql"]).parent.name
    if folder != entry["split"]:
        errors.append(f"split is {entry['split']} but gold_sql is in {folder}/")
    rows = run(con, entry)
    exp, tol, qtype = entry["expected"], entry["tolerance"], entry["type"]

    if qtype == "numeric":
        if len(rows) != 1:
            errors.append(f"expected 1 row, got {len(rows)}")
        else:
            cmp_values("expected", exp, rows[0], tol, errors)

    elif qtype == "ranking" and "interpretations" in exp:
        for name, exp_rows in exp["interpretations"].items():
            act = [{k: v for k, v in r.items() if k != "interpretation"}
                   for r in rows if r.get("interpretation") == name]
            act.sort(key=lambda r: r["rnk"])
            cmp_rows(name, exp_rows, act, tol, errors)
        extra = {r.get("interpretation") for r in rows} - set(exp["interpretations"])
        if extra:
            errors.append(f"unexpected interpretations in gold result: {sorted(map(str, extra))}")
        tie = exp.get("tie_at_cutoff")
        if tie:  # the last ranked row must sit at the cutoff value, and the tied count must cover the tied rows shown
            ranked = [r for r in rows if r.get("interpretation") == "by_orders"]
            ranked.sort(key=lambda r: r["rnk"])
            if ranked and ranked[-1][tie["column"]] != tie["value"]:
                errors.append(f"tie_at_cutoff.value: expected {tie['value']}, last row has {ranked[-1][tie['column']]}")
            shown = sum(r[tie["column"]] == tie["value"] for r in ranked)
            if tie["tied_customers"] < shown:
                errors.append(f"tie_at_cutoff.tied_customers {tie['tied_customers']} < {shown} tied rows in the top 10")

    elif qtype == "ranking":
        cmp_rows("rows", exp["rows"], rows, tol, errors)

    elif qtype == "set":
        (key,) = exp["key"]
        for bucket, spec in exp["sets"].items():
            act = {r[key] for r in rows if r["percentile_bucket"] == bucket}
            if len(act) != spec["count"]:
                errors.append(f"{bucket}: expected count {spec['count']}, got {len(act)}")
            missing, extra = set(spec["seller_ids"]) - act, act - set(spec["seller_ids"])
            if missing or extra:
                errors.append(f"{bucket}: {len(missing)} ids missing, {len(extra)} unexpected")
        extra_buckets = {r["percentile_bucket"] for r in rows} - set(exp["sets"])
        if extra_buckets:
            errors.append(f"unexpected buckets: {sorted(extra_buckets)}")

    elif qtype == "behavior":
        facts = exp["facts"]
        if "money_columns" in facts:  # q015: proof query lists columns
            n_cost = sum(r["kind"] == "cost_or_profit" for r in rows)
            cmp_values("facts", {"cost_or_profit_columns": facts["cost_or_profit_columns"]},
                       {"cost_or_profit_columns": n_cost}, tol, errors)
            if facts["money_columns"] != rows:
                errors.append(f"facts.money_columns: expected {facts['money_columns']}, got {rows}")
        elif len(rows) != 1:
            errors.append(f"expected 1 row, got {len(rows)}")
        else:
            cmp_values("facts", facts, rows[0], tol, errors)

    else:
        errors.append(f"unknown type {qtype}")
    return errors


def main(argv):
    entries = yaml.safe_load(QUESTIONS.read_text())
    if argv:
        entries = [e for e in entries if e["id"] in argv]
    con = duckdb.connect(str(DB), read_only=True)
    failed = 0

    referenced = {e["gold_sql"] for e in yaml.safe_load(QUESTIONS.read_text())}
    on_disk = {p.relative_to(ROOT).as_posix() for p in GOLD.glob("*/*.sql")}
    for path in sorted(on_disk - referenced):
        print(f"FAIL {path} is not referenced by questions.yaml")
        failed += 1
    for path in sorted(referenced - on_disk):
        print(f"FAIL {path} is referenced by questions.yaml but missing")
        failed += 1
    for entry in entries:
        try:
            errors = check(con, entry)
        except Exception as exc:  # a broken query counts as a failure
            errors = [f"{type(exc).__name__}: {exc}"]
        status = "OK  " if not errors else "FAIL"
        print(f"{status} {entry['id']} ({entry['split']}, {entry['type']})")
        for err in errors:
            print(f"       {err}")
        failed += bool(errors)
    print(f"\n{failed} failure(s) across {len(entries)} questions and the gold file check")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
