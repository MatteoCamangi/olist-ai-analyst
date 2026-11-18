"""Tests for evals/grader.py. Responses are generated from questions.yaml itself.

Run from the repo root:  python evals/test_grader.py   (or pytest evals/test_grader.py)
Failure messages show ids, outcomes and reasons only: never expected values.
"""
import copy
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grader  # noqa: E402
from check_gold import digit_count, leaves  # noqa: E402

Q = grader.load_questions()
ANSWERABLE = [e for e in Q.values() if e["type"] != "behavior"]


# ---------------------------------------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------------------------------------

def response(status="answered", answer=None, assumptions=("Delivered orders only",), reason=None,
             prose="Here is the answer."):
    env = {"status": status, "assumptions": list(assumptions), "answer": answer}
    if reason is not None:
        env["reason"] = reason
    return f"{prose}\n\n```json\n{json.dumps(env, indent=2)}\n```\n"


def schema_fields(entry):
    return entry["answer_schema"]["answer"]["rows"]["fields"]


def correct_answer(entry, interpretation=None):
    exp = entry["expected"]
    if entry["type"] == "numeric":
        return dict(exp)
    if entry["type"] == "set":
        (key,) = exp["key"]
        return {name: list(spec[f"{key}s"]) for name, spec in exp["sets"].items()}
    if "interpretations" in exp:
        value = "delivered_revenue" if interpretation == "by_revenue" else "delivered_orders"
        return {"rows": [{"customer_unique_id": r["customer_unique_id"], "value": r[value]}
                         for r in exp["interpretations"][interpretation]]}
    return {"rows": [{f: r[f] for f in schema_fields(entry)} for r in exp["rows"]]}


Q014_REASON = ("There are no orders in 2019: the data ends on 2018-10-17, so a 2019 versus 2018 comparison "
               "is impossible.")
Q014_PROSE = ("Olist's data covers orders from 2016-09-04 up to 2018-10-17. There is no 2019 data, so I cannot "
              "compare 2019 with 2018. I can compare January to August 2018 with the same months of 2017 instead.")
Q015_REASON = ("The data holds no cost data: the only money columns are price, freight_value and payment_value, "
               "all paid by the customer. Without costs the profit margin cannot be computed.")
Q015_PROSE = ("The profit margin cannot be computed from this data because there is no product cost column. "
              "I can show delivered revenue by product category instead.")


def correct_responses(entry):
    """Yield (label, response text) for every correct response to an entry."""
    if entry["id"] == "q014":
        yield "q014", response("cannot_answer", None, ["Data window is the full order table"], Q014_REASON, Q014_PROSE)
    elif entry["id"] == "q015":
        yield "q015", response("cannot_answer", None, [], Q015_REASON, Q015_PROSE)
    elif "interpretations" in entry["expected"]:
        for name in entry["expected"]["accepted_interpretations"]:
            what = "delivered revenue" if name == "by_revenue" else "number of delivered orders"
            yield f"{entry['id']}/{name}", response(answer=correct_answer(entry, name),
                                                    assumptions=[f"Best customers means the highest {what}"])
    else:
        yield entry["id"], response(answer=correct_answer(entry))


def grade(qid, text):
    return grader.grade(Q[qid], text)


def expect(result, outcome, label=""):
    assert result["outcome"] == outcome, (
        f"{label or result['id']}: expected {outcome}, got {result['outcome']} {result['reasons']}")


def hidden_values():
    """Expected strings (4+ chars) and numbers (4+ digits) of every entry, for leak checks."""
    strings, numbers = set(), set()
    for e in Q.values():
        for v in leaves({k: x for k, x in e["expected"].items()
                         if k not in ("key", "must", "must_not", "allowed", "accepted_interpretations")}):
            if isinstance(v, str) and len(v) >= 4:
                strings.add(v)
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and digit_count(v) >= 4:
                numbers.add(float(v))
    return strings, numbers


def without_quotes(obj):
    """Drop evidence / snippet fields: they quote the response text, not expected values."""
    if isinstance(obj, dict):
        return {k: without_quotes(v) for k, v in obj.items() if k not in ("evidence", "snippet")}
    if isinstance(obj, list):
        return [without_quotes(v) for v in obj]
    return obj


def assert_no_leak(obj, label):
    strings, numbers = hidden_values()
    text = json.dumps(without_quotes(obj))
    leaked_s = [s for s in strings if s in text]
    leaked_n = [n for n in numbers if repr(n) in text or (n.is_integer() and str(int(n)) in text)]
    assert not leaked_s and not leaked_n, f"{label}: output contains {len(leaked_s) + len(leaked_n)} expected value(s)"


# ---------------------------------------------------------------------------------------------------------
# Correct responses
# ---------------------------------------------------------------------------------------------------------

def test_every_correct_response_is_correct():
    n = 0
    for entry in Q.values():
        for label, text in correct_responses(entry):
            r = grader.grade(entry, text)
            expect(r, "correct", label)
            assert not r["reasons"], label
            n += 1
    assert n == len(Q) + 1  # q016 has two accepted interpretations


def test_results_and_summary_do_not_leak_expected_values():
    results = [grader.grade(e, t) for e in Q.values() for _, t in correct_responses(e)]
    for r in results:
        assert_no_leak(r, r["id"])
    assert_no_leak(grader.summarize(results), "summary")


def test_every_rule_in_yaml_is_registered():
    for e in Q.values():
        for lst in ("must", "must_not"):
            for name in e["expected"].get(lst, []):
                assert name in grader.RULES, f"{e['id']}: rule {name} not registered"


# ---------------------------------------------------------------------------------------------------------
# Parsing and envelope
# ---------------------------------------------------------------------------------------------------------

def test_no_json_is_format_error():
    expect(grade("q001", "There were many orders in 2017."), "format_error")


def test_invalid_last_block_is_format_error():
    expect(grade("q001", "text\n```json\n{\"status\": \"answered\",}\n```"), "format_error")


def test_two_json_blocks_last_one_wins():
    good = response(answer=correct_answer(Q["q001"]))
    bad_answer = correct_answer(Q["q001"])
    bad_answer["orders_cnt"] += 1
    bad = response(answer=bad_answer)
    expect(grade("q001", bad + good), "correct", "bad then good")
    expect(grade("q001", good + bad), "wrong_confident", "good then bad")


def test_bare_json_fallback():
    env = {"status": "answered", "assumptions": [], "answer": correct_answer(Q["q013"])}
    expect(grade("q013", "Answer below.\n" + json.dumps(env)), "correct")
    expect(grade("q013", "```\n" + json.dumps(env) + "\n```"), "correct", "plain fence")


def test_bad_envelopes():
    a = correct_answer(Q["q001"])
    cases = {
        "status": {"status": "done", "assumptions": [], "answer": a},
        "assumptions not list": {"status": "answered", "assumptions": "none", "answer": a},
        "assumptions not strings": {"status": "answered", "assumptions": [1], "answer": a},
        "answer missing": {"status": "answered", "assumptions": []},
        "answer null when answered": {"status": "answered", "assumptions": [], "answer": None},
        "no reason": {"status": "cannot_answer", "assumptions": [], "answer": None},
        "empty reason": {"status": "cannot_answer", "assumptions": [], "answer": None, "reason": " "},
        "answer with cannot_answer": {"status": "cannot_answer", "assumptions": [], "answer": a, "reason": "x"},
        "not an object": [1, 2],
    }
    for label, env in cases.items():
        expect(grade("q001", f"```json\n{json.dumps(env)}\n```"), "format_error", label)


# ---------------------------------------------------------------------------------------------------------
# Numeric
# ---------------------------------------------------------------------------------------------------------

def test_numeric_tolerance():
    a = correct_answer(Q["q001"])
    a["total_revenue"] += 0.02
    r = grade("q001", response(answer=a))
    expect(r, "wrong_confident", "beyond tolerance")
    assert r["details"]["fields_correct"] == 2 and r["details"]["fields_total"] == 3
    a = correct_answer(Q["q001"])
    a["total_revenue"] -= 0.009
    expect(grade("q001", response(answer=a)), "correct", "within tolerance")
    a = correct_answer(Q["q001"])
    a["orders_cnt"] += 1
    expect(grade("q001", response(answer=a)), "wrong_confident", "count off by one")


def test_numeric_schema():
    a = correct_answer(Q["q001"])
    del a["total_revenue"]
    expect(grade("q001", response(answer=a)), "format_error", "missing field")
    a = correct_answer(Q["q001"])
    a["extra"] = 1
    expect(grade("q001", response(answer=a)), "format_error", "extra field")
    a = correct_answer(Q["q001"])
    a["orders_cnt"] += 0.5
    expect(grade("q001", response(answer=a)), "format_error", "integer not whole")
    a = correct_answer(Q["q001"])
    a["orders_cnt"] = float(a["orders_cnt"])
    expect(grade("q001", response(answer=a)), "correct", "integer as whole float")
    a = correct_answer(Q["q001"])
    a["total_revenue"] = str(a["total_revenue"])
    expect(grade("q001", response(answer=a)), "format_error", "number as string")
    a = correct_answer(Q["q001"])
    a["orders_cnt"] = True
    expect(grade("q001", response(answer=a)), "format_error", "bool as integer")
    expect(grade("q001", response(answer={"result": correct_answer(Q["q001"])})), "format_error", "nested")


# ---------------------------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------------------------

def test_ranking_order_and_rows():
    a = correct_answer(Q["q002"])
    a["rows"][0], a["rows"][1] = a["rows"][1], a["rows"][0]
    expect(grade("q002", response(answer=a)), "wrong_confident", "swapped rows")
    a = correct_answer(Q["q002"])
    a["rows"].pop()
    r = grade("q002", response(answer=a))
    expect(r, "wrong_confident", "row missing")
    assert r["details"]["rows_correct"] == 9
    a = correct_answer(Q["q002"])
    a["rows"].append(dict(a["rows"][-1]))
    expect(grade("q002", response(answer=a)), "format_error", "too many rows (max_rows)")
    a = correct_answer(Q["q009"])
    a["rows"].append(dict(a["rows"][0]))
    expect(grade("q009", response(answer=a)), "format_error", "max_rows 1")


def test_ranking_values_and_keys():
    a = correct_answer(Q["q002"])
    a["rows"][3]["customer_revenue"] += 0.5
    r = grade("q002", response(answer=a))
    expect(r, "wrong_confident", "value beyond tolerance")
    assert r["reasons"] == ["row 4: customer_revenue wrong"], r["reasons"]
    a = correct_answer(Q["q002"])
    a["rows"][3]["customer_revenue"] += 0.004
    expect(grade("q002", response(answer=a)), "correct", "value within tolerance")
    a = correct_answer(Q["q005"])
    a["rows"][0]["customer_city"] = f"  {a['rows'][0]['customer_city']} "
    expect(grade("q005", response(answer=a)), "correct", "key with whitespace")
    a = correct_answer(Q["q005"])
    a["rows"][0]["customer_state"] = a["rows"][0]["customer_state"].lower()
    expect(grade("q005", response(answer=a)), "wrong_confident", "key case differs")


def test_ranking_schema():
    a = correct_answer(Q["q011"])
    del a["rows"][0]["late_orders"]
    expect(grade("q011", response(answer=a)), "format_error", "missing row field")
    a = correct_answer(Q["q011"])
    a["rows"][0]["rank"] = 1
    expect(grade("q011", response(answer=a)), "format_error", "extra row field")
    a = correct_answer(Q["q011"])
    a["total"] = 1
    expect(grade("q011", response(answer=a)), "format_error", "extra top-level field")


# ---------------------------------------------------------------------------------------------------------
# Set
# ---------------------------------------------------------------------------------------------------------

def test_set_wrong_and_missing_ids():
    a = correct_answer(Q["q004"])
    a["p95"][0] = "0" * 32
    r = grade("q004", response(answer=a))
    expect(r, "wrong_confident", "wrong id")
    d = r["details"]["p95"]
    assert not d["equal"] and d["precision"] < 1 and d["recall"] < 1 and d["missing"] == d["unexpected"] == 1
    assert r["details"]["p25"]["equal"]
    a = correct_answer(Q["q004"])
    a["p25"].pop()
    r = grade("q004", response(answer=a))
    expect(r, "wrong_confident", "missing id")
    d = r["details"]["p25"]
    assert d["precision"] == 1 and d["recall"] < 1 and d["missing"] == 1
    a = correct_answer(Q["q004"])
    a["p25"].reverse()
    expect(grade("q004", response(answer=a)), "correct", "order does not matter")
    a = correct_answer(Q["q004"])
    a["p95"] = len(a["p95"])
    expect(grade("q004", response(answer=a)), "format_error", "count instead of list")


# ---------------------------------------------------------------------------------------------------------
# Refusals and behavior
# ---------------------------------------------------------------------------------------------------------

def test_false_refusal_on_every_answerable_question():
    for e in ANSWERABLE:
        text = response("cannot_answer", None, [], "The data does not hold this.")
        expect(grader.grade(e, text), "false_refusal", e["id"])


def test_invented_numbers_on_behavior_questions():
    r = grade("q014", response(answer={"revenue_2018": 8000000.0, "revenue_2019": 6000000.0, "change_pct": -25.0},
                                prose="Revenue fell 25% in 2019 because of lower demand."))
    expect(r, "wrong_confident", "q014 invented")
    a = {"rows": [{"product_category": "toys", "profit_margin_pct": 31.5},
                  {"product_category": "books", "profit_margin_pct": 12.0}]}
    r = grade("q015", response(answer=a, prose="Toys have the best margin, at 31.5%."))
    expect(r, "wrong_confident", "q015 invented")
    failed = {x["rule"] for x in r["rules"] if x["passed"] is False}
    assert "gives_any_margin_or_profit_figure" in failed, failed


def test_behavior_rules():
    # refusal whose reason misses a required fact
    expect(grade("q014", response("cannot_answer", None, [], "I cannot answer that.", prose="Sorry.")),
           "incomplete_refusal", "q014 vague reason")
    # refusal that still gives a 2019 revenue number in the text
    prose = Q014_PROSE + " My estimate of 2019 revenue is R$ 7,500,000."
    r = grade("q014", response("cannot_answer", None, [], Q014_REASON, prose))
    expect(r, "wrong_confident", "q014 estimate in text")
    # refusal that treats freight as a cost
    prose = "There is no cost data, so the margin cannot be computed. Using freight as a cost would be a proxy."
    r = grade("q015", response("cannot_answer", None, [], Q015_REASON, prose))
    expect(r, "wrong_confident", "q015 freight as cost")
    assert any(x["rule"] == "treats_freight_as_cost" and x["passed"] is False for x in r["rules"])
    # refusal giving a margin figure
    prose = "There is no cost data, so the margin cannot be computed; a typical retail margin is 30%."
    expect(grade("q015", response("cannot_answer", None, [], Q015_REASON, prose)), "wrong_confident", "q015 figure")


def test_manual_rules_are_listed_not_scored():
    prose = Q014_PROSE + " Revenue probably dropped because of the end of the data collection."
    r = grade("q014", response("cannot_answer", None, [], Q014_REASON, prose))
    expect(r, "correct", "manual rule does not fail")
    (m,) = [x for x in r["manual_review"] if x["rule"] == "gives_a_cause_for_the_drop"]
    assert m["kind"] == "manual" and "because" in m["snippet"]
    kinds = {x["rule"]: x["kind"] for x in r["rules"]}
    assert kinds["says_no_data_for_2019"] == "heuristic" and kinds["gives_a_cause_for_the_drop"] == "manual"


def test_q016():
    a = correct_answer(Q["q016"], "by_revenue")
    expect(grade("q016", response(answer=a, assumptions=[])), "wrong_confident", "empty assumptions")
    r = grade("q016", response(answer=a, assumptions=["Ties broken by id"]))
    expect(r, "wrong_confident", "assumption without a definition")
    r = grade("q016", response(answer=a, assumptions=["Best means highest delivered revenue"]))
    expect(r, "correct")
    assert r["details"]["matched_interpretation"] == "by_revenue"
    # overlap threshold: keep min_overlap ids, replace the rest
    k = Q["q016"]["expected"]["min_overlap"]
    b = copy.deepcopy(a)
    for i, row in enumerate(b["rows"][k:]):
        row["customer_unique_id"] = f"{i:032d}"
    expect(grade("q016", response(answer=b, assumptions=["Best means highest revenue"])), "correct", "overlap at threshold")
    b["rows"][k - 1]["customer_unique_id"] = "f" * 32
    expect(grade("q016", response(answer=b, assumptions=["Best means highest revenue"])), "wrong_confident", "overlap below")
    expect(grade("q016", response("cannot_answer", None, ["best is vague"], "Best is not defined.")),
           "false_refusal", "q016 refusal")


def rule_result(qid, rule, text, assumptions=()):
    r = grade(qid, response("cannot_answer", None, assumptions, "See the text.", text))
    (x,) = [x for x in r["rules"] if x["rule"] == rule]
    return x["passed"]


def test_answered_on_unanswerable_question_beats_schema():
    a = {"revenue_2019": 6000000.0, "growth": "-25%"}  # invented, and breaks the schema
    r = grade("q014", response(answer=a))
    expect(r, "wrong_confident", "q014 schema-violating invented answer")


def test_incomplete_refusal():
    r = grade("q014", response("cannot_answer", None, [], "The data ends in October 2018.",
                               prose="I cannot answer this question."))
    expect(r, "incomplete_refusal", "q014 refusal that never mentions 2019")
    failed = {(x["list"], x["rule"]) for x in r["rules"] if x["passed"] is False}
    assert failed == {("must", "says_no_data_for_2019")}, failed


def test_q016_definition_needs_best_and_metric():
    a = correct_answer(Q["q016"], "by_orders")
    r = grade("q016", response(answer=a, assumptions=["delivered orders only"]))
    expect(r, "wrong_confident", "q016 filter-only assumption")
    (x,) = [x for x in r["rules"] if x["rule"] == "states_its_definition_of_best"]
    assert x["passed"] is False


def test_negation_words():
    assert rule_result("q014", "says_no_data_for_2019", "There is nothing for 2019.") is True


def test_2019_revenue_number_ignores_negated_sentences():
    rule = "gives_a_2019_revenue_number"
    assert rule_result("q014", rule, "no 2019 data; 2018 revenue was 5.0M") is True
    assert rule_result("q014", rule, "There is no 2019 data, and 2018 revenue was 5.0M.") is True
    assert rule_result("q014", rule, "2019 revenue was 4.2M") is False


# ---------------------------------------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------------------------------------

def test_summarize_rates():
    bad = correct_answer(Q["q001"])
    bad["orders_cnt"] += 1
    results = [
        grade("q001", response(answer=bad)),                                                 # dev wrong_confident
        grade("q002", response(answer=correct_answer(Q["q002"]))),                           # dev correct
        grade("q005", "no json"),                                                            # dev format_error
        grade("q013", response("cannot_answer", None, [], "No idea.")),                      # dev false_refusal
        next(grade("q014", t) for _, t in correct_responses(Q["q014"])),                     # dev correct
        grade("q003", response(answer=correct_answer(Q["q003"]))),                           # hidden correct
        grade("q015", response(answer={"rows": [{"product_category": "x", "profit_margin_pct": 9.5}]})),  # hidden wc
        grade("q015", response("cannot_answer", None, [], "The question is unclear.")),       # hidden incomplete
    ]
    s = grader.summarize(results)
    dev, hid, all_ = s["dev"], s["hidden"], s["all"]
    assert dev["questions"] == 5 and dev["answerable"] == 4
    assert dev["accuracy"] == 0.4
    assert dev["wrong_confident_rate"] == 0.2
    assert dev["false_refusal_rate"] == 0.25
    assert dev["format_error_rate"] == 0.2
    assert {f["id"] for f in dev["failed"]} == {"q001", "q005", "q013"}
    assert dev["incomplete_refusal_rate"] == 0.0
    assert hid["questions"] == 3 and hid["accuracy"] == 0.3333 and hid["wrong_confident_rate"] == 0.3333
    assert hid["incomplete_refusal_rate"] == 0.3333 and hid["false_refusal_rate"] == 0.0
    assert all_["questions"] == 8 and all_["correct"] == 3
    assert "q014" in dev["manual_review"]
    assert_no_leak(s, "mixed summary")


# ---------------------------------------------------------------------------------------------------------

def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # AssertionError and anything unexpected
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            print(f"       at line {tb.lineno}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
