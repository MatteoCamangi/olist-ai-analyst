# Evals

Question set and expected answers for grading the analyst agent.

## Layout

```
evals/
  gold/dev/q*.sql      gold queries for the dev split (8 questions)
  gold/hidden/q*.sql   gold queries for the hidden split (8 questions)
  questions.yaml       expected answers, one entry per question (q001 ... q016)
  ANSWER_FORMAT.md     answer format the analyst agent must use
  check_gold.py        reruns every gold query and compares it with questions.yaml
  grader.py            grades one agent response against questions.yaml (never inside the agent's sandbox)
  test_grader.py       tests for grader.py, with responses generated from questions.yaml
  cases/, results/     reserved for agent runs (empty for now)
```

Each entry in `questions.yaml` has `id`, `split`, `type`, `question`, `gold_sql`, `answer_schema`, `expected` and `tolerance`.
Types:

- `numeric`: named values from the single result row.
- `ranking`: `key` (list of key columns) and ordered `rows`. Keys must match in order. Other columns are compared within tolerance.
  q016 is ambiguous and stores two `interpretations` (`by_revenue`, `by_orders`), `min_overlap`
  (top-10 overlap needed to match an interpretation) and its behavior rules.
- `set`: q004 stores the `p95` and `p25` seller_id sets and their counts.
- `behavior`: q014 and q015 store the `facts` from the proof query and the `must`, `must_not` and `allowed` lists.

Tolerances are absolute: counts 0, money 0.01, percentages 0.01 points, average days 0.05.
Strings (ids, dates) are always compared exactly.

## Dev and hidden split

The analyst agent may see the dev questions only. **It must never read `evals/gold/hidden/` or any entry of
`questions.yaml` with `split: hidden`.** The grader reads both splits. Results on the hidden split are the ones that count.

## Answer format

The agent answers in the format described in [ANSWER_FORMAT.md](ANSWER_FORMAT.md): a short text answer, then one JSON
block with `status`, `assumptions`, `answer` and (when it cannot answer) `reason`.
ANSWER_FORMAT.md is part of the agent's context (it contains no eval values), so scores measure the agent together
with that document.

Every entry has an `answer_schema` with field names and types only. **`question` and `answer_schema` are safe to show
to the agent for every question, hidden ones included.** Everything else in an entry (`expected`, `tolerance`,
`gold_sql`) and the files in `evals/gold/hidden/` are not. `check_gold.py` checks that each schema's field names
match the keys in `expected`, that no string value from `expected` appears in a schema, and that no expected
string or number with 4 or more digits appears in ANSWER_FORMAT.md.

## Checking the gold answers

From the repo root, with the venv active:

```
python evals/check_gold.py              # all questions
python evals/check_gold.py q004 q016    # some questions
```

It opens `warehouse.duckdb` read-only, reruns each `gold_sql` and exits with code 1 on any difference,
or if a file in `evals/gold/*/` is not referenced by `questions.yaml`.
Run it after any change to the raw data or a gold query. If it fails, find out why before you edit `questions.yaml`.

## Grader

`grader.py` grades one response: `grade(entry, response_text)` returns a result dict, and `summarize(results)`
turns a list of results into rates. Pure Python: no database, no network, no LLM.

```
python evals/grader.py --question q001 --response-file path/to/response.md
python evals/test_grader.py             # tests (or pytest evals/test_grader.py)
```

**The grader reads every entry of `questions.yaml`, hidden ones included. `grader.py`, `test_grader.py`,
`questions.yaml` and `evals/gold/hidden/` must never be inside the analyst agent's sandbox, and the agent must never
run the grader.** Result dicts and summaries hold flags, counts and rates, never expected values; only the
`evidence` and `snippet` fields quote the response text.

Steps: take the last ```` ```json ```` block (or else the last top-level JSON object), check the envelope
(ANSWER_FORMAT.md), check the answer against `answer_schema` (exact field names, types, `max_rows`), then score by
type. Each question gets exactly one outcome:

| Outcome | When |
|---|---|
| `correct` | Right status and every field, row or set right; every scored rule passes. |
| `wrong_confident` | Status `answered` with wrong values, or `answered` on a question that cannot be answered. Also a refusal on q014/q015 that fails a must / must_not rule. |
| `false_refusal` | Status `cannot_answer` on a question that can be answered. |
| `format_error` | No valid JSON, a broken envelope (an empty `reason` included) or an answer that breaks `answer_schema`. |

Behavior and q016 rules live in the `RULES` registry in `grader.py`, each marked `exact`, `heuristic`
(keyword/regex) or `manual`. Manual rules (for example `gives_a_cause_for_the_drop`) are not scored: they are listed
under `manual_review` with the matching text, for a human to read.

`summarize` reports, per split and overall: `accuracy` and `wrong_confident_rate` and `format_error_rate` over all
questions of the split, `false_refusal_rate` over answerable questions only (q014 and q015 excluded), and the failed
ids with their outcomes and reasons. The hidden split is the one that counts.
