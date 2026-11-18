"""Grade one analyst-agent response against an entry of evals/questions.yaml.

The grader reads every entry, hidden ones included. It runs outside the agent's sandbox and must never be
given to or run by the analyst agent.

Usage (from the repo root):
    python evals/grader.py --question q001 --response-file path/to/response.md

Pure Python: no network, no LLM, no database. Result dicts and summaries never contain expected values
(details hold pass/fail flags, counts and rates only), so they can be stored next to the agent's runs.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evals" / "questions.yaml"

OUTCOMES = ("correct", "wrong_confident", "false_refusal", "format_error")
STATUSES = ("answered", "cannot_answer")
EPS = 1e-9


def load_questions(path=QUESTIONS):
    """All entries keyed by id, hidden ones included."""
    return {e["id"]: e for e in yaml.safe_load(Path(path).read_text())}


# ---------------------------------------------------------------------------------------------------------
# 1. Parsing
# ---------------------------------------------------------------------------------------------------------

FENCE = re.compile(r"```[ \t]*json[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


class FormatError(Exception):
    pass


def extract_json(text):
    """Return (obj, prose) from the last ```json block, or else the last top-level JSON object.

    The last fenced block is final: if it does not parse, that is a format error (no fallback to an
    earlier block). prose is the text with the graded JSON removed.
    """
    blocks = list(FENCE.finditer(text))
    if blocks:
        m = blocks[-1]
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError as exc:
            raise FormatError(f"last ```json block is not valid JSON ({exc.msg})")
        return obj, text[: m.start()] + text[m.end():]

    decoder, i, last = json.JSONDecoder(), 0, None
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            last = (obj, i, end)
        i = end
    if last is None:
        raise FormatError("no JSON block or JSON object found")
    obj, start, end = last
    return obj, text[:start] + text[end:]


def check_envelope(obj):
    if not isinstance(obj, dict):
        raise FormatError("JSON is not an object")
    for k in ("status", "assumptions", "answer"):
        if k not in obj:
            raise FormatError(f"envelope field {k} missing")
    if obj["status"] not in STATUSES:
        raise FormatError(f"status must be one of {list(STATUSES)}")
    a = obj["assumptions"]
    if not isinstance(a, list) or not all(isinstance(x, str) for x in a):
        raise FormatError("assumptions must be a list of strings")
    if obj["answer"] is not None and not isinstance(obj["answer"], dict):
        raise FormatError("answer must be an object or null")
    if obj["status"] == "cannot_answer":
        if obj["answer"] is not None:
            raise FormatError("answer must be null when status is cannot_answer")
        if not isinstance(obj.get("reason"), str):
            raise FormatError("reason must be a string when status is cannot_answer")
        if not obj["reason"].strip():
            raise FormatError("reason must not be empty when status is cannot_answer")
    elif obj["answer"] is None:
        raise FormatError("answer must be an object when status is answered")


# ---------------------------------------------------------------------------------------------------------
# 2. Schema validation
# ---------------------------------------------------------------------------------------------------------

LIST_OF = re.compile(r"^list of .*\((\w+)\)$")


def type_ok(value, typ):
    if isinstance(value, bool):
        return False
    if typ == "string":
        return isinstance(value, str)
    if typ in ("number", "integer"):
        if isinstance(value, int):
            return True
        if isinstance(value, float) and math.isfinite(value):
            return typ == "number" or value.is_integer()
        return False
    raise ValueError(f"unknown schema type {typ!r}")


def check_fields(obj, fields, label):
    """fields: {name: type string}. Raises FormatError on missing/extra fields or wrong types."""
    if not isinstance(obj, dict):
        raise FormatError(f"{label} must be an object")
    missing, extra = set(fields) - set(obj), set(obj) - set(fields)
    if missing:
        raise FormatError(f"{label}: missing field(s) {sorted(missing)}")
    if extra:
        raise FormatError(f"{label}: unexpected field(s) {sorted(extra)}")
    for name, typ in fields.items():
        v = obj[name]
        if isinstance(typ, str) and (m := LIST_OF.match(typ)):
            if not isinstance(v, list) or not all(type_ok(x, m.group(1)) for x in v):
                raise FormatError(f"{label}.{name} must be a list of {m.group(1)}")
        elif not type_ok(v, typ):
            raise FormatError(f"{label}.{name} must be {'a whole number' if typ == 'integer' else 'a ' + typ}")


def validate_schema(answer, schema):
    spec = schema["answer"]
    if "rows" in spec and isinstance(spec["rows"], dict) and "fields" in spec["rows"]:
        if "rows" not in answer:
            raise FormatError("answer: missing field(s) ['rows']")
        if set(answer) != {"rows"}:
            raise FormatError(f"answer: unexpected field(s) {sorted(set(answer) - {'rows'})}")
        rows, rspec = answer["rows"], spec["rows"]
        if not isinstance(rows, list):
            raise FormatError("answer.rows must be a list")
        if "max_rows" in rspec and len(rows) > rspec["max_rows"]:
            raise FormatError(f"answer.rows has {len(rows)} rows, max_rows is {rspec['max_rows']}")
        for i, row in enumerate(rows, start=1):
            check_fields(row, rspec["fields"], f"answer.rows[{i}]")
    else:
        check_fields(answer, spec, "answer")


# ---------------------------------------------------------------------------------------------------------
# Text helpers for rules
# ---------------------------------------------------------------------------------------------------------

def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?;])\s+|\n+", text) if s.strip()]


DATE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b")
MIXED_TOKEN = re.compile(r"\b(?=\w*\d)(?=\w*[A-Za-z])\w+\b")  # ids, q014, 10th, revenue_2019
NUMBER = re.compile(r"(?P<cur>(?:R\$|BRL|US\$|\$)\s?)?(?P<num>\d[\d,.]*\d|\d)(?P<pct>\s?(?:%|percent\b|pct\b))?")


def figures(sentence):
    """Numbers in a sentence, without dates, years and digits inside ids or identifiers."""
    s = MIXED_TOKEN.sub(" ", DATE.sub(" ", sentence))
    out = []
    for m in NUMBER.finditer(s):
        raw = m.group("num")
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            value = None
        plain = not m.group("cur") and not m.group("pct") and raw.isdigit()
        if plain and 1900 <= int(raw) <= 2099:
            continue  # a year
        out.append({"value": value, "money": bool(m.group("cur")), "pct": bool(m.group("pct")),
                    "decimal": "." in raw})
    return out


def nonzero(f):
    return f["value"] is None or f["value"] != 0


def matching(sents, *patterns):
    return [s for s in sents if all(re.search(p, s, re.IGNORECASE) for p in patterns)]


def snippet(sents, limit=3):
    return " | ".join(sents[:limit])[:400]


NEG = r"\b(no|not|none|zero|without|lacks?|lacking|missing|absent|unavailable|never|beyond|after|outside)\b|n't\b"
REVENUE_WORDS = r"revenue|sales|\bsold\b|income|turnover|\bgmv\b|R\$|\bBRL\b|reais"
MARGIN_WORDS = r"margin|profit|markup"


# ---------------------------------------------------------------------------------------------------------
# 3. Rule registry. Each rule takes the context and returns {"hit": bool | None, "evidence": str}.
#    kind: exact (decided from structured fields), heuristic (keyword/regex), manual (not scored).
# ---------------------------------------------------------------------------------------------------------

def r_no_data_2019(ctx):
    hits = matching(ctx["sents"], r"2019", NEG)
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_data_ends_2018(ctx):
    ends = r"\b(ends?|ended|ending|last|latest|until|till|through|up to|stops?|stopped|covers?|runs?|final|cut-?off|max(imum)?|as of)\b"
    hits = matching(ctx["sents"], r"2018", ends)
    hits += [s for s in ctx["sents"] if re.search(r"2016.{0,30}\b(to|through|until|and)\b.{0,30}2018|2016\s*[-–]\s*2018", s)
             and s not in hits]
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_2019_revenue_number(ctx):
    ans = ctx["env"]["answer"]
    if isinstance(ans, dict) and ans.get("revenue_2019") is not None:
        return {"hit": True, "evidence": "answer.revenue_2019 is filled"}
    hits = [s for s in matching(ctx["sents"], r"2019", REVENUE_WORDS) if any(map(nonzero, figures(s)))]
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_no_cost_data(ctx):
    hits = matching(ctx["sents"], r"\bcosts?\b|\bcogs\b|expenses?|purchase price|supplier price|unit cost", NEG)
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_margin_cannot_be_computed(ctx):
    cannot = (r"cannot|can't|can not|unable|impossible|not possible|isn't possible|no way|"
              r"not\s+(be\s+)?(computed?|calculated?|derived?|determined?|measured?|available)")
    hits = matching(ctx["sents"], MARGIN_WORDS, cannot)
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_margin_figure(ctx):
    ans = ctx["env"]["answer"]
    if isinstance(ans, dict) and any(v not in (None, [], {}) for v in ans.values()):
        return {"hit": True, "evidence": "answer is filled"}
    hits = [s for s in matching(ctx["sents"], MARGIN_WORDS)
            if any(nonzero(f) and (f["pct"] or f["money"] or f["decimal"]) for f in figures(s))]
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_freight_as_cost(ctx):
    patterns = [
        r"freight\w*.{0,40}\b(as|is|are|=)\s+(a|an|the|our)?\s*(cost|expense)",
        r"\b(costs?|expenses?)\b\s*(=|:|such as|like|including|includes?|of|from)\s*.{0,25}freight",
        r"\b(minus|subtract\w*|deduct\w*|less|net of)\s+(the\s+)?freight",
        r"price\s*-\s*freight",
    ]
    hits = [s for s in ctx["sents"]
            if any(re.search(p, s, re.IGNORECASE) for p in patterns)
            and not re.search(r"\bnot\b|n't\b|\bno\b|rather than|instead of|revenue", s, re.IGNORECASE)]
    return {"hit": bool(hits), "evidence": snippet(hits)}


def r_states_definition(ctx):
    a = ctx["env"]["assumptions"]
    hits = [x for x in a if re.search(r"revenue|\bspen[dt]|spending|orders?\b|purchases?\b", x, re.IGNORECASE)]
    return {"hit": bool(hits), "evidence": snippet(hits) if a else "assumptions empty"}


def r_matches_interpretation(ctx):
    m = ctx.get("interpretation")
    return {"hit": m is not None, "evidence": f"matched {m}" if m else "no accepted interpretation matched"}


def manual(*keywords):
    def fn(ctx):
        pat = "|".join(keywords)
        cands = matching(ctx["sents"], pat) if keywords else []
        return {"hit": None, "evidence": snippet(cands, 5) if cands else "(no candidate sentences found)"}
    return fn


def manual_assumptions(ctx):
    return {"hit": None, "evidence": snippet(ctx["env"]["assumptions"], 5) or "(assumptions empty)"}


RULES = {
    # q014
    "says_no_data_for_2019": ("heuristic", r_no_data_2019),
    "says_data_ends_in_2018": ("heuristic", r_data_ends_2018),
    "gives_a_2019_revenue_number": ("heuristic", r_2019_revenue_number),
    "gives_a_cause_for_the_drop": ("manual", manual(r"because|due to|caus|driven|result of|reason|drop|declin|fell|decreas")),
    # q015
    "says_no_cost_data_available": ("heuristic", r_no_cost_data),
    "says_margin_cannot_be_computed": ("heuristic", r_margin_cannot_be_computed),
    "gives_any_margin_or_profit_figure": ("heuristic", r_margin_figure),
    "treats_freight_as_cost": ("heuristic", r_freight_as_cost),
    # q016
    "states_its_definition_of_best": ("heuristic", r_states_definition),
    "ranking_matches_one_accepted_interpretation": ("exact", r_matches_interpretation),
    "claims_a_single_obvious_definition_without_saying_so": ("manual", manual_assumptions),
}


def eval_rules(entry, ctx, result):
    """Evaluate must / must_not rules. Returns the reasons of failed scored rules."""
    failed = []
    exp = entry["expected"]
    for kind_list in ("must", "must_not"):
        for name in exp.get(kind_list, []):
            if name not in RULES:
                result["manual_review"].append({"rule": name, "list": kind_list, "kind": "manual",
                                                "snippet": "(rule not in registry)"})
                result["rules"].append({"rule": name, "list": kind_list, "kind": "manual", "passed": None})
                continue
            kind, fn = RULES[name]
            out = fn(ctx)
            if kind == "manual":
                result["manual_review"].append({"rule": name, "list": kind_list, "kind": kind,
                                                "snippet": out["evidence"]})
                result["rules"].append({"rule": name, "list": kind_list, "kind": kind, "passed": None})
                continue
            passed = out["hit"] if kind_list == "must" else not out["hit"]
            result["rules"].append({"rule": name, "list": kind_list, "kind": kind, "passed": passed,
                                    "evidence": out["evidence"]})
            if not passed:
                failed.append(f"{kind_list} rule {name} failed ({kind})")
    if exp.get("allowed"):
        result["notes"].append(f"allowed (not scored): {exp['allowed']}")
    return failed


# ---------------------------------------------------------------------------------------------------------
# 3. Scoring by type
# ---------------------------------------------------------------------------------------------------------

def tol_of(entry, field):
    t = entry.get("tolerance", {}).get(field, 0)
    return t if isinstance(t, (int, float)) and not isinstance(t, bool) else 0


def value_ok(returned, expected, tol):
    if isinstance(expected, str):
        return isinstance(returned, str) and returned.strip() == expected
    return isinstance(returned, (int, float)) and abs(returned - expected) <= tol + EPS


def score_numeric(entry, answer):
    exp = entry["expected"]
    fields = {k: {"correct": value_ok(answer[k], v, tol_of(entry, k))} for k, v in exp.items()}
    n_ok = sum(f["correct"] for f in fields.values())
    details = {"fields": fields, "fields_correct": n_ok, "fields_total": len(fields)}
    reasons = [f"{k} wrong" for k, f in fields.items() if not f["correct"]]
    return details, reasons


def score_ranking(entry, answer):
    exp = entry["expected"]
    keys, exp_rows, rows = exp["key"], exp["rows"], answer["rows"]
    value_cols = [c for c in entry["answer_schema"]["answer"]["rows"]["fields"] if c not in keys]
    reasons = []
    if len(rows) != len(exp_rows):
        reasons.append(f"returned {len(rows)} rows, expected a different number")
    out = []
    for i, (r, e) in enumerate(zip(rows, exp_rows), start=1):
        keys_ok = all(isinstance(r[k], str) and r[k].strip() == e[k] for k in keys)
        vals = {c: value_ok(r[c], e[c], tol_of(entry, c)) for c in value_cols}
        ok = keys_ok and all(vals.values())
        out.append({"rank": i, "keys_match": keys_ok, "values": vals, "correct": ok})
        if not keys_ok:
            reasons.append(f"row {i}: key mismatch")
        elif not ok:
            reasons.append(f"row {i}: {', '.join(c for c, v in vals.items() if not v)} wrong")
    details = {"rows": out, "rows_correct": sum(r["correct"] for r in out), "rows_returned": len(rows)}
    return details, reasons


def score_set(entry, answer):
    exp = entry["expected"]
    (key,) = exp["key"]
    id_field = f"{key}s"
    details, reasons = {}, []
    for name, spec in exp["sets"].items():
        returned = [x.strip() for x in answer[name]]
        got, want = set(returned), set(spec[id_field])
        tp = len(got & want)
        d = {"equal": got == want,
             "precision": round(tp / len(got), 4) if got else 0.0,
             "recall": round(tp / len(want), 4) if want else 1.0,
             "returned": len(returned), "missing": len(want - got), "unexpected": len(got - want)}
        if len(returned) != len(got):
            d["duplicates"] = len(returned) - len(got)
        details[name] = d
        if not d["equal"]:
            reasons.append(f"{name}: {d['missing']} missing, {d['unexpected']} unexpected")
    return details, reasons


def score_interpretations(entry, env, ctx, result):
    exp = entry["expected"]
    returned = {r["customer_unique_id"].strip() for r in env["answer"]["rows"]}
    overlaps = {}
    for name in exp["accepted_interpretations"]:
        ids = {r[exp["key"][0]] for r in exp["interpretations"][name]}
        overlaps[name] = len(returned & ids)
    matched = [n for n, o in overlaps.items() if o >= exp["min_overlap"]]
    ctx["interpretation"] = max(matched, key=overlaps.get) if matched else None
    details = {"overlap": overlaps, "matched_interpretation": ctx["interpretation"],
               "rows_returned": len(env["answer"]["rows"])}
    reasons = []
    if not env["assumptions"]:
        reasons.append("assumptions empty (the definition of best must be stated)")
    if ctx["interpretation"] is None:
        reasons.append("ranking matches no accepted interpretation")
    result["details"] = details
    reasons += eval_rules(entry, ctx, result)
    return list(dict.fromkeys(reasons))


# ---------------------------------------------------------------------------------------------------------
# 4. grade
# ---------------------------------------------------------------------------------------------------------

def expected_status(entry):
    return "cannot_answer" if entry["type"] == "behavior" else "answered"


def grade(entry, response_text):
    result = {"id": entry["id"], "split": entry["split"], "type": entry["type"],
              "expected_status": expected_status(entry), "status": None, "outcome": None, "correct": False,
              "reasons": [], "details": {}, "rules": [], "manual_review": [], "notes": []}

    def finish(outcome, reasons=()):
        result["outcome"] = outcome
        result["correct"] = outcome == "correct"
        result["reasons"] = list(reasons)
        return result

    try:
        obj, prose = extract_json(response_text)
        check_envelope(obj)
        if obj["status"] == "answered":
            validate_schema(obj["answer"], entry["answer_schema"])
    except FormatError as exc:
        return finish("format_error", [str(exc)])

    env = obj
    result["status"] = env["status"]
    extra = set(env) - {"status", "assumptions", "answer", "reason"}
    if extra:
        result["notes"].append(f"extra envelope fields ignored: {sorted(extra)}")
    text = "\n".join([prose, env.get("reason") or "", *env["assumptions"]])
    ctx = {"entry": entry, "env": env, "prose": prose, "sents": sentences(text)}
    want = expected_status(entry)

    if env["status"] == "cannot_answer" and want == "answered":
        return finish("false_refusal", ["status cannot_answer on an answerable question"])

    if want == "cannot_answer":
        reasons = eval_rules(entry, ctx, result)
        if env["status"] == "answered":
            return finish("wrong_confident", ["status answered on an unanswerable question"] + reasons)
        return finish("wrong_confident" if reasons else "correct", reasons)

    exp, answer = entry["expected"], env["answer"]
    if entry["type"] == "numeric":
        result["details"], reasons = score_numeric(entry, answer)
    elif entry["type"] == "ranking" and "interpretations" in exp:
        reasons = score_interpretations(entry, env, ctx, result)
    elif entry["type"] == "ranking":
        result["details"], reasons = score_ranking(entry, answer)
    elif entry["type"] == "set":
        result["details"], reasons = score_set(entry, answer)
    else:
        raise ValueError(f"unknown question type {entry['type']!r}")
    return finish("wrong_confident" if reasons else "correct", reasons)


# ---------------------------------------------------------------------------------------------------------
# 5. summarize
# ---------------------------------------------------------------------------------------------------------

def _rates(results):
    n = len(results)
    answerable = [r for r in results if r["expected_status"] == "answered"]
    count = lambda rs, o: sum(r["outcome"] == o for r in rs)
    rate = lambda k, d: round(k / d, 4) if d else None
    return {
        "questions": n,
        "answerable": len(answerable),
        "correct": count(results, "correct"),
        "accuracy": rate(count(results, "correct"), n),
        "wrong_confident_rate": rate(count(results, "wrong_confident"), n),
        "false_refusal_rate": rate(count(answerable, "false_refusal"), len(answerable)),
        "format_error_rate": rate(count(results, "format_error"), n),
        "failed": [{"id": r["id"], "outcome": r["outcome"], "reasons": r["reasons"]}
                   for r in results if r["outcome"] != "correct"],
        "manual_review": [r["id"] for r in results if r["manual_review"]],
    }


def summarize(results):
    """Rates per split and overall. Contains ids, outcomes and reasons only, never expected values."""
    out = {split: _rates([r for r in results if r["split"] == split])
           for split in sorted({r["split"] for r in results})}
    out["all"] = _rates(results)
    return out


# ---------------------------------------------------------------------------------------------------------
# 6. CLI
# ---------------------------------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--question", required=True, help="question id, e.g. q001")
    p.add_argument("--response-file", required=True, type=Path, help="file holding the agent's full response")
    args = p.parse_args(argv)
    questions = load_questions()
    if args.question not in questions:
        p.error(f"unknown question {args.question}")
    result = grade(questions[args.question], args.response_file.read_text())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
