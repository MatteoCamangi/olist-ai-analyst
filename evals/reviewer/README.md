# Reviewer test cases

Labelled cases for the future reviewer agent described in [agent/REVIEWER_BRIEF.md](../../agent/REVIEWER_BRIEF.md):
it reads one analyst answer (question, response, trace) and returns approve, revise or reject with the checks that
failed.

**Dev only.** Every case comes from a dev question (q001, q002, q005, q010, q013, q014, q017, q018, q019, q020) in a
dev run. Nothing is read from, copied from or points to a hidden question, a hidden run or a hidden detail entry.
**The held-out hidden failures are deliberately not included**: they stay unseen so the reviewer can later be
measured on them.

```
evals/reviewer/
  cases.json       the cases (committed)
  make_faults.py   builds cases.json from local dev detail files and injects the faults
  test_cases.py    checks the cases (no database needed)
```

## Cases

17 cases, sorted by id:

| Origin | Count | Source | Expected verdict |
|---|---|---|---|
| natural | 10 | answers of the full-context dev run `20261121T174710Z_dev_v3-harder` (12/12 correct), untouched | approve |
| natural | 1 | q010 of `20261122T170925Z_dev_grid-raw-bare` (raw scope, no docs, minimal brief): late rate on timestamps, AL 23.93% instead of 21.41% | reject (definitions) |
| injected | 6 | a copy of one approve case with deterministic edits, one per fault type | reject or revise |

q007 and q011 of the v3-harder run are left out. Their traces list a few ids (rows past the dev cut-off) that are
also hidden expected values and are neither dev expected values nor in the public docs. Including them untouched
would put hidden answer values in this folder.

## Schema

`cases.json` is a list of objects with exactly these fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `<question>.ok` (approve), `q010.raw-bare` (natural failure), `<question>.<fault type>` (injected) |
| `source_question` | string | dev question id |
| `split` | string | always `dev` |
| `question` | string | the question text from questions.yaml |
| `answer_schema` | object | the answer schema the analyst received |
| `response` | string | the analyst's full response (prose, then the JSON envelope) |
| `trace` | list | the analyst's trace: `sql` events (the queries), `tool_result` events (their results), `text` and `final` |
| `origin` | string | `natural` (a real answer, untouched) or `injected` |
| `fault_type` | string or null | the injected fault, null for natural cases |
| `base_case` | string or null | the approve case the fault was injected into |
| `edits` | list or null | the exact edits applied to the base case (see below) |
| `expected_verdict` | string | `approve`, `revise` or `reject` |
| `expected_failed_checks` | list | empty for approve; otherwise check names from the list below |
| `source` | object | `run`, `git_sha`, `agent_config`, graded `outcome` and `sha256` of the original response and trace. An injected case keeps its base's source |
| `note` | string | one sentence on why the case has this verdict |

No expected values, gold SQL or grader reasons are copied into the cases.

## Checks and verdicts

Check names follow section 3 of the reviewer brief: `numbers`, `definitions`, `grain_population`, `assumptions`,
`refusals_false_premises`, `claims_beyond_data`, `format`. Verdicts follow its section 4: **reject** when a finding
makes the headline answer wrong or unsupported, **revise** when the headline stays right but a stakeholder could be
misled, **approve** when there are no findings.

## Injected faults

`make_faults.py` declares each fault as exact string edits `{scope, old, new, count}`. Scope `sql` edits the trace's
SQL, `result` the query result, `text` the response and the trace's `text`/`final` events (which hold the same text).
Each edit must match exactly `count` times, or the build stops. When the SQL is edited, the new numbers are the real
result of the edited query: the build re-runs the original and the edited SQL read-only on warehouse.duckdb and stops
if either differs from the result written in its trace. No model is called and no agent runs.

| Fault | Base | Edit | Verdict | Failed check |
|---|---|---|---|---|
| `wrong_filter` | q013 | canceled filter widened to canceled + unavailable; 625 → 1,234 orders, 0.63% → 1.24% | reject | definitions |
| `threshold_off_by_one` | q001 | `BETWEEN ... '2017-12-31'` becomes `< '2017-12-31'`; 45,101 → 45,027 orders and the other figures follow | reject | grain_population |
| `unsupported_claim` | q019 | adds a share of 5-star reviews that no query computed | revise | claims_beyond_data |
| `invented_cause` | q010 | replaces the association caveat with an invented cause | revise | claims_beyond_data |
| `missing_assumption` | q018 | removes the "performs best = revenue" definition and the assumptions; the number stays | revise | assumptions |
| `contradicting_assumption` | q020 | states that revenue includes freight and all statuses (docs/metrics.md: item price, delivered orders) | revise | assumptions |

## Rebuilding and tests

The detail files are gitignored, so only a machine with the two dev runs can rebuild:

```
python evals/reviewer/make_faults.py           # writes cases.json and prints the case table
python evals/reviewer/make_faults.py --check   # exit 1 if cases.json differs from a fresh build
python evals/reviewer/test_cases.py            # tests
```

The script opens only files named `*_dev_*` and stops on any item that is not a dev question. `test_cases.py` checks:
the required fields and values; that verdicts follow the brief; that no hidden question id appears in any file here,
and no hidden question text, gold path or hidden-only expected value appears in cases.json or this README; that
natural cases are byte-identical to the dev originals (by hash, and against the detail file when it is present); that
each injected case is exactly its base plus its declared edits; and that a fresh build gives the same file (skipped
without the detail files or the database).

Like the rest of evals/, this folder must never be inside the analyst agent's sandbox.
