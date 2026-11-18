"""Rerun every gold query and compare its result with evals/questions.yaml.

Usage (from the repo root):  python evals/check_gold.py [q001 q014 ...]
Exit code 0 when every checked question matches, 1 on any difference or error.
The database is opened read-only.
"""
import datetime as dt
import re
import sys
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "warehouse.duckdb"
QUESTIONS = ROOT / "evals" / "questions.yaml"
GOLD = ROOT / "evals" / "gold"
ANSWER_FORMAT = ROOT / "evals" / "ANSWER_FORMAT.md"  # shown to the agent


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


def leaves(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from leaves(x)
    else:
        yield v


def check_schema(entry, errors):
    """answer_schema must exist, match the expected field names (and max_rows for rankings), and never contain expected values."""
    schema = entry.get("answer_schema")
    if not isinstance(schema, dict) or "answer" not in schema:
        errors.append("answer_schema missing or has no answer")
        return
    exp, qtype, answer = entry["expected"], entry["type"], schema["answer"]
    if qtype == "numeric":
        want, got = set(exp), set(answer)
    elif qtype == "ranking" and "interpretations" not in exp:
        want = {k for row in exp["rows"] for k in row}
        got = set(answer["rows"]["fields"])
        if answer["rows"].get("max_rows") != len(exp["rows"]):
            errors.append(f"answer_schema max_rows {answer['rows'].get('max_rows')!r} differs from "
                          f"{len(exp['rows'])} expected rows")
    elif qtype == "set":
        want, got = set(exp["sets"]), set(answer)
    else:
        want = got = None  # behavior and q016: free-form schemas, no field match
    if want != got:
        errors.append(f"answer_schema fields {sorted(got)} differ from expected keys {sorted(want)}")
    text = yaml.safe_dump(schema)
    values = {k: v for k, v in exp.items() if k != "key"}  # key lists column names, not values
    leaked = {v for v in leaves(values) if isinstance(v, str) and len(v) >= 4 and v in text}
    if leaked:
        errors.append(f"answer_schema contains expected values: {sorted(leaked)}")


def digit_count(x):
    return len(repr(float(x)).removesuffix(".0").replace(".", "").replace("-", ""))


def doc_leaks(entries, text):
    """Expected values that appear in a document the agent sees.

    Strings (ids, cities, states, dates) match as whole words, case-sensitive. Numbers with 4 or more
    digits match any number in the text, with thousands separators removed. `key` lists are column
    names, not values, and are skipped.
    """
    strings, numbers = set(), set()
    for e in entries:
        for v in leaves({k: x for k, x in e["expected"].items() if k != "key"}):
            if isinstance(v, bool):
                continue
            if isinstance(v, str):
                strings.add(v)
            elif isinstance(v, (int, float)) and digit_count(v) >= 4:
                numbers.add(float(v))
    hits = {s for s in strings if re.search(rf"(?<![\w-]){re.escape(s)}(?![\w-])", text)}
    for tok in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        tok = tok.rstrip(",").replace(",", "")
        if tok and float(tok) in numbers:
            hits.add(tok)
    return sorted(hits)


def check(con, entry):
    errors = []
    check_schema(entry, errors)
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
    leaks = doc_leaks(yaml.safe_load(QUESTIONS.read_text()), ANSWER_FORMAT.read_text())
    if leaks:
        print(f"FAIL {ANSWER_FORMAT.relative_to(ROOT)} contains expected values: {leaks}")
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
    print(f"\n{failed} failure(s) across {len(entries)} questions, the gold file check and the ANSWER_FORMAT.md leak check")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
