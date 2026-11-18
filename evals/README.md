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
