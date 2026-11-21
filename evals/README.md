# Evals

Question set and expected answers for grading the analyst agent.

## Layout

```
evals/
  gold/dev/q*.sql      gold queries for the dev split (12 questions)
  gold/hidden/q*.sql   gold queries for the hidden split (12 questions)
  questions.yaml       expected answers, one entry per question (q001 ... q024)
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
  q016 (best customers, top 10) and q018 (best category, top 1) are ambiguous and store two `interpretations`
  (`by_revenue`, `by_orders`), `min_overlap` (overlap of the `key` ids needed to match an interpretation) and their
  behavior rules. Their schema has the key column and a `value` column that does not name the metric.
- `set`: `sets` maps each answer field to its ids (`<key>s`) and `count`. q004 has two sets (`p95`, `p25`, gold rows
  split by `percentile_bucket`); q022 (`sellers`) and q023 (`products`) have one.
- `behavior`: q014, q015, q017 and q021 store the `facts` from the proof query and the `must`, `must_not` and
  `allowed` lists. Their schemas look answerable on purpose.

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
match the keys in `expected`, that no string value from `expected` and no expected number with 4 or more digits
appears in a schema, and that no expected string or number with 4 or more digits appears in ANSWER_FORMAT.md.

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
| `wrong_confident` | Status `answered` with wrong values, or `answered` on a question that cannot be answered (decided before schema validation, so an invented answer is never a `format_error`). Also a refusal on a behavior question where a must_not rule fired. |
| `incomplete_refusal` | Status `cannot_answer` on a behavior question, no must_not rule fired, but a scored must rule failed (the refusal misses a required fact). |
| `false_refusal` | Status `cannot_answer` on a question that can be answered. |
| `format_error` | No valid JSON, a broken envelope (an empty `reason` included) or an answer that breaks `answer_schema`. |

Behavior, q016 and q018 rules live in the `RULES` registry in `grader.py`, each marked `exact`, `heuristic`
(keyword/regex) or `manual`. Manual rules (for example `gives_a_cause_for_the_drop`) are not scored: they are listed
under `manual_review` with the matching text, for a human to read.

`summarize` reports, per split and overall: `accuracy` (correct only), `wrong_confident_rate`,
`incomplete_refusal_rate` and `format_error_rate` over all questions of the split, `false_refusal_rate` over answerable questions only (behavior questions excluded), and the failed
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
agent command, number of questions, counts and rates per split, usage totals and `agent_config`) and
`evals/runs/detail/<same name>.json` (per-question results and raw responses). Commit the summaries; the detail
folder is gitignored.

**Agent configuration (ablations).** `agent/analyst.py` reads three environment variables, so the same code can run
with parts removed. Unset or empty means the default, which is the v1 agent (the same prompt, byte for byte, and model):

| Variable | Values | Effect |
|---|---|---|
| `ANALYST_MODEL` | model id starting with `claude-` (default `claude-sonnet-5-5`) | the model called |
| `ANALYST_DOCS` | `full` (default), `schema`, `none` | `full`: docs/metrics.md and the marts yml docs. `schema`: the marts yml model and column descriptions only (doc() blocks resolved), no docs/metrics.md. `none`: no documents; the agent finds tables through information_schema |
| `ANALYST_BRIEF` | `full` (default), `minimal` | `minimal` drops the rules between the `<!-- rules:start -->` / `<!-- rules:end -->` markers of agent/BRIEF.md (section 4); role, tool, limits and the answer-format instruction stay |

evals/ANSWER_FORMAT.md is in the prompt in every mode, and the agent's allowed-documents list does not change. An
invalid value exits 2 before any API call. The agent writes the configuration in its USAGE line
(`"model"`, `"docs"`, `"brief"`); the runner reads it from the first question and stores one `agent_config` object in
the summary (null with `--responses-dir` or an agent that does not report it). Label runs after the ablation:

```
ANALYST_DOCS=schema python evals/run_eval.py --split dev --label docs-schema --agent-cmd "python agent/analyst.py"
ANALYST_DOCS=none ANALYST_BRIEF=minimal python evals/run_eval.py --split dev --label bare --agent-cmd "python agent/analyst.py"
```

**Hidden runs.** `--split hidden` and `--split all` are refused without `--confirm-hidden`. For these splits the
console and the summary hold rates only: no question ids, outcomes or reasons, so failures cannot be tuned against.
The one exception is the reason text of `format_error` entries (infrastructure failures such as output limits or
timeouts, which never contain expected values), read without question ids. Any change made after such a look is
recorded in the run label.
The summary records `earlier_hidden_runs`, the number of hidden or all runs already in `evals/runs/`: the more hidden
runs, the less the hidden score is worth. Iterate on dev; run hidden rarely, at milestones. Do not open hidden detail
files while working on the agent.

**Denominators.** `accuracy`, `wrong_confident_rate`, `incomplete_refusal_rate` and `format_error_rate` are over all
questions of the split; `false_refusal_rate` is over answerable questions only (10 of 12 per split: q014 and q017
in dev, q015 and q021 in hidden are not answerable). With 12 questions per split, one question moves accuracy by
8.3 points: read small differences
between runs as noise.

**Heuristic rules.** The behavior, q016 and q018 rules are keyword and regex checks. They can miss a correct refusal
worded in an unusual way (a false `incomplete_refusal`) or let through a figure written in words or split across
sentences. The q017 and q021 must_not rules also read clauses (split at ", so", ", but" and similar), so a negation
in one clause does not hide a figure in the next.
Before trusting a behavior result, read the rule `evidence` and the `manual_review` snippets in the detail file
(for hidden runs, only after the agent work it would inform is finished).
