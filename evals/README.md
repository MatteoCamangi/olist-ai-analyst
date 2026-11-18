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
  run_eval.py          runs the agent on a split, grades it and saves the run (never inside the agent's sandbox)
  test_runner.py       tests for run_eval.py, with fake agents
  runs/*.json          one summary per run: counts and rates only (committed, the score history)
  runs/detail/         per-question results and raw responses (gitignored)
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
type. Each question gets exactly one of five outcomes:

| Outcome | When |
|---|---|
| `correct` | Right status and every field, row or set right; every scored rule passes. |
| `wrong_confident` | Status `answered` with wrong values, or `answered` on a question that cannot be answered (decided before schema validation, so an invented answer is never a `format_error`). Also a refusal on q014/q015 where a must_not rule fired. |
| `incomplete_refusal` | Status `cannot_answer` on q014/q015, no must_not rule fired, but a scored must rule failed (the refusal misses a required fact). |
| `false_refusal` | Status `cannot_answer` on a question that can be answered. |
| `format_error` | No valid JSON, a broken envelope (an empty `reason` included) or an answer that breaks `answer_schema`. |

Behavior and q016 rules live in the `RULES` registry in `grader.py`, each marked `exact`, `heuristic`
(keyword/regex) or `manual`. Manual rules (for example `gives_a_cause_for_the_drop`) are not scored: they are listed
under `manual_review` with the matching text, for a human to read.

`summarize` reports, per split and overall: `accuracy` (correct only), `wrong_confident_rate`,
`incomplete_refusal_rate` and `format_error_rate` over all questions of the split, `false_refusal_rate` over answerable questions only (q014 and q015 excluded), and the failed
ids with their outcomes and reasons. The hidden split is the one that counts.

## Running an eval

```
python evals/run_eval.py --split dev --label baseline --agent-cmd "python agent/main.py"
python evals/run_eval.py --split dev --label quick --questions q001,q014 --agent-cmd "..."
python evals/run_eval.py --split dev --label replay --responses-dir path/to/responses   # grades DIR/<id>.md
python evals/run_eval.py --split hidden --label baseline --agent-cmd "..." --confirm-hidden
python evals/test_runner.py                                                             # tests
```

For each question the runner starts `--agent-cmd` in a fresh process, writes one JSON object
`{"question": ..., "answer_schema": ...}` to its stdin and reads the response text from stdout. Nothing else from
`questions.yaml` is passed. A crash (non-zero exit), a timeout (`--timeout`, default 300 seconds) or empty output is
graded `format_error` with reason `agent failed: ...`. `--questions` takes a subset of dev questions only.

Each run writes `evals/runs/<UTC timestamp>_<split>_<label>.json` (timestamp, split, label, git sha and dirty flag,
agent command, number of questions, counts and rates per split) and `evals/runs/detail/<same name>.json` (per-question
results and raw responses). Commit the summaries; the detail folder is gitignored.

**Hidden runs.** `--split hidden` and `--split all` are refused without `--confirm-hidden`. For these splits the
console and the summary hold rates only: no question ids, outcomes or reasons, so failures cannot be tuned against.
The summary records `earlier_hidden_runs`, the number of hidden or all runs already in `evals/runs/`: the more hidden
runs, the less the hidden score is worth. Iterate on dev; run hidden rarely, at milestones. Do not open hidden detail
files while working on the agent.

**Denominators.** `accuracy`, `wrong_confident_rate`, `incomplete_refusal_rate` and `format_error_rate` are over all
questions of the split; `false_refusal_rate` is over answerable questions only (7 of 8 per split: q014 and q015 are
not answerable). With 8 questions per split, one question moves accuracy by 12.5 points: read small differences
between runs as noise.

**Heuristic rules.** The behavior and q016 rules are keyword and regex checks. They can miss a correct refusal worded
in an unusual way (a false `incomplete_refusal`) or let through a figure written in words or split across sentences.
Before trusting a behavior result, read the rule `evidence` and the `manual_review` snippets in the detail file
(for hidden runs, only after the agent work it would inform is finished).
