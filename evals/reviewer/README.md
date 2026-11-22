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

29 cases in two batches. The first 17 (batch 1) are kept byte-identical and first in the file; the 12 of batch 2
were appended after them. Each batch is sorted by id.

| Origin | Count | Source | Expected verdict |
|---|---|---|---|
| natural | 10 | answers of the full-context dev run `20261121T174710Z_dev_v3-harder` (12/12 correct), untouched | approve |
| natural | 1 | q010 of `20261122T170925Z_dev_grid-raw-bare` (raw scope, no docs, minimal brief): late rate on timestamps, AL 23.93% instead of 21.41% | reject (definitions) |
| injected | 18 | a copy of one approve case with deterministic edits: 10 fault types, two variants on different bases for eight of them | reject or revise |

**Verdict counts: approve 10, revise 10, reject 9.**

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
Scope `text_all` (`{scope, new}`) replaces that whole text, for rewrites such as a refusal. Each edit must match
exactly `count` times, or the build stops. When the SQL is edited, the new numbers are the real result of the edited
query: the build re-runs the original and the edited SQL read-only on warehouse.duckdb and stops if either differs
from the result written in its trace. An added claim that must be true carries `claim_sql` and `claim_rows`, which the
build re-runs read-only. No model is called and no agent runs.

| Fault | Batch | Base | Edit | Verdict | Failed checks |
|---|---|---|---|---|---|
| `wrong_filter` | 1 | q013 | canceled filter widened to canceled + unavailable; 625 → 1,234 orders, 0.63% → 1.24% | reject | definitions |
| `wrong_filter` (silent) | 2 | q018 | `WHERE is_delivered` → `WHERE order_status <> 'canceled'`; health_beauty 1,233,131.72 → 1,255,695.13 and the other figures follow. The assumptions are rewritten to agree with the SQL ("All orders except canceled ones") and the question names no population, so only docs/metrics.md (product metrics use delivered orders) shows the error. The stated assumption contradicts the docs, so `assumptions` fails too | reject | definitions, assumptions |
| `threshold_off_by_one` | 1 | q001 | `BETWEEN ... '2017-12-31'` becomes `< '2017-12-31'`; 45,101 → 45,027 orders and the other figures follow | reject | grain_population |
| `threshold_off_by_one` | 2 | q020 | top-10 cut `LIMIT 10` → `LIMIT 11`; 13.27% → 14.20% while the text still says top 10 | reject | grain_population |
| `unsupported_claim` | 1 | q019 | adds a share of 5-star reviews that no query computed | revise | claims_beyond_data |
| `unsupported_claim` | 2 | q001 | adds "November was the busiest month of 2017": true of the data (7,544 orders, checked by `claim_sql`) but no query in the trace looks at months. No digits, so `numbers` does not fire | revise | claims_beyond_data |
| `invented_cause` | 1 | q010 | replaces the association caveat with an invented cause | revise | claims_beyond_data |
| `invented_cause` | 2 | q005 | "São Paulo leads because Olist's warehouses are located there" | revise | claims_beyond_data |
| `missing_assumption` | 1 | q018 | removes the "performs best = revenue" definition and the assumptions; the number stays | revise | assumptions |
| `missing_assumption` | 2 | q013 | empties the assumptions, so the canceled vs unavailable definition is no longer stated | revise | assumptions |
| `contradicting_assumption` | 1 | q020 | states that revenue includes freight and all statuses (docs/metrics.md: item price, delivered orders) | revise | assumptions |
| `contradicting_assumption` | 2 | q010 | states that late is compared as timestamps (docs/metrics.md: calendar days); SQL and numbers unchanged | revise | assumptions |
| `number_mismatch` | 2 | q005 | rio de janeiro 6,601 → 6,610 in text and JSON; the query returned 6,601 | reject | numbers |
| `number_mismatch` | 2 | q019 | average 4.16 (4.156) → 4.18 (4.176); the query returned 4.156 | reject | numbers |
| `false_refusal` | 2 | q002 | rewritten as "cannot be answered from the data" (no person-level customer id), while the trace groups by customer_unique_id and returns the ranking | reject | refusals_false_premises |
| `false_premise_accepted` | 2 | q014 | the query also returns delivered revenue 2018 (7,218,125.12, real) and 2019 (0); the answer accepts the premise: "a 100% fall because no orders were placed in 2019", status answered | reject | refusals_false_premises |
| `format_break` | 2 | q013 | a sentence after the JSON block (ANSWER_FORMAT.md: nothing comes after it) | revise | format |
| `format_break` | 2 | q020 | the percentage written as the string `"13.27%"` (ANSWER_FORMAT.md: a plain number) | revise | format |

Why the verdicts differ: wrong_filter, threshold_off_by_one and number_mismatch change the headline figure, and
false_refusal withholds an answer the data supports, so they are reject. **false_premise_accepted is reject**: the
headline (revenue fell 100% in 2019) is unsupported, because 2019 is simply absent from the data (it ends 2018-10-17)
and 2018 is partial. A missing year is not a drop, so this is a high finding that makes the headline answer
unsupported, the brief's reject rule. The remaining faults leave the headline right and mislead around it, so they
are revise; format_break is revise because the figures are right but the answer breaks the spec.

Known labelling limit: `q019.unsupported_claim` (batch 1) adds "More than 60% ...", a figure in no query result, so
under the brief it arguably fails `numbers` as well; it is labelled `claims_beyond_data` only and stays unchanged
because batch 1 is frozen.

## Coverage

Every check of the brief's section 3 is expected in at least two cases:

| Check | Cases | Case ids |
|---|---|---|
| numbers | 2 | q005.number_mismatch, q019.number_mismatch |
| definitions | 3 | q010.raw-bare, q013.wrong_filter, q018.wrong_filter |
| grain_population | 2 | q001.threshold_off_by_one, q020.threshold_off_by_one |
| assumptions | 5 | q018.missing_assumption, q020.contradicting_assumption, q010.contradicting_assumption, q013.missing_assumption, q018.wrong_filter |
| refusals_false_premises | 2 | q002.false_refusal, q014.false_premise_accepted |
| claims_beyond_data | 4 | q010.invented_cause, q019.unsupported_claim, q001.unsupported_claim, q005.invented_cause |
| format | 2 | q013.format_break, q020.format_break |

## Rebuilding and tests

The detail files are gitignored, so only a machine with the two dev runs can rebuild:

```
python evals/reviewer/make_faults.py           # writes cases.json, prints the case and coverage tables
python evals/reviewer/make_faults.py --check   # exit 1 if cases.json differs from a fresh build
python evals/reviewer/test_cases.py            # tests
```

The script opens only files named `*_dev_*` and stops on any item that is not a dev question. `test_cases.py` checks:
the required fields and values; that the first 17 cases are unchanged and first (frozen sha256); that each fault
type has its variants on different bases; that every check of the brief's section 3 is expected in at least two
cases; that verdicts follow the brief; that no hidden question id appears in any file here,
and no hidden question text, gold path or hidden-only expected value appears in cases.json or this README; that
natural cases are byte-identical to the dev originals (by hash, and against the detail file when it is present); that
each injected case is exactly its base plus its declared edits; and that a fresh build gives the same file (skipped
without the detail files or the database).

Like the rest of evals/, this folder must never be inside the analyst agent's sandbox.
