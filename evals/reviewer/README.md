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
| natural | 10 | answers of the full-context dev run `20261121T174710Z_dev_v3-harder` (12/12 correct), untouched | approve (7), revise (3: q001.ok, q010.ok and q018.ok, see Label corrections) |
| natural | 1 | q010 of `20261122T170925Z_dev_grid-raw-bare` (raw scope, no docs, minimal brief): late rate on timestamps, AL 23.93% instead of 21.41% | reject (definitions) |
| injected | 18 | a copy of one approve case with deterministic edits: 10 fault types, two variants on different bases for eight of them | reject or revise |

**Verdict counts: approve 7, revise 13, reject 9** (before the label corrections of 2026-11-24: approve 10, revise 10).

q007 and q011 of the v3-harder run are left out. Their traces list a few ids (rows past the dev cut-off) that are
also hidden expected values and are neither dev expected values nor in the public docs. Including them untouched
would put hidden answer values in this folder.

## Label corrections

Three natural cases were relabelled after reading reviewer findings. These are post-hoc changes, so each is recorded
in the case (`label_history`) and every summary reports metrics under both label sets.

| Case | Was | Now | Found by | Reason |
|---|---|---|---|---|
| q001.ok | approve | revise, claims_beyond_data | reviewer v1 findings | The response says "The data has no orders before 2017-01-05", but query 1 is filtered to 2017 and docs/metrics.md says the data starts 2016-09-04. It also says "Early 2017 also reflects the marketplace ramp-up", which no query supports. |
| q018.ok | approve | revise, claims_beyond_data | reviewer v1 findings | The response says bed_bath_table would lead by volume, but query 1 ranks categories by revenue with LIMIT 5, so categories outside the revenue top 5 were never compared by volume. |
| q010.ok | approve | revise, claims_beyond_data | reviewer v2 findings (v1 had approved it) | The assumption states that a minimum volume of 5 was applied and that every state passes it; query 1 has no HAVING and uses LIMIT 8, so neither is shown in the trace. |

All three are dated 2026-11-24. Sources: "corrected after reading reviewer v1 findings" (q001.ok, q018.ok) and "found
by the v2 runs after reading findings; v1 had approved it" (q010.ok). The headline answers are right; the flaws are
claims around them, so the verdict is revise.

**Bias risk.** Every correction so far came from reading the reviewer's own findings, and each one moved a label
toward what the reviewer said, on the dev cases the reviewer is tuned on. That inflates the corrected-label scores of
whichever reviewer prompted the change. Mitigations: a label changes only with a reason that points to the trace or
the docs; the original-label view is always reported next to the corrected one; hidden questions are never relabelled;
and the held-out run is the test that counts. q010.ok also shows the limit of the labels: v2 flags it under
`assumptions`, while the label says `claims_beyond_data` (a statement of volume no result supports), so
`check_recall` counts it as a miss even though the verdict is right.

**Metrics under two label sets.** Summaries report the corrected labels as the headline (`metrics`, `per_fault_type`,
`per_check`, `confusion`, `label_view: "corrected"`) and the original labels under `original_labels`. Scores before
and after the corrections stay comparable.

**Injected cases on these bases are not single-fault cases.** q001.threshold_off_by_one, q001.unsupported_claim,
q018.missing_assumption, q018.wrong_filter, q010.invented_cause and q010.contradicting_assumption were built from
q001.ok, q018.ok and q010.ok and inherit their unsupported claims. Their labels are unchanged: they are judged on the
injected fault, and a reviewer that also flags the inherited claim is not penalised (extra flags cost nothing in
`check_recall`).

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
| `label_history` | list (only on corrected cases) | each correction: `date`, `from` and `to` (`expected_verdict`, `expected_failed_checks`, `note`), `reason`, `source` |

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

## Reviewer agent and runner

`agent/reviewer.py` reviews one case: JSON on stdin (or a file path), the verdict JSON on stdout, and a final stderr
line `USAGE {"input_tokens": n, "output_tokens": n, "model": s}`. It makes exactly one model call with no tools.
The system prompt is agent/REVIEWER_BRIEF.md, docs/metrics.md and evals/ANSWER_FORMAT.md, verbatim and in that order,
sent as one cached block (docs/data_notes.md is not included). The user message is built only from `question`,
`answer_schema`, `response` and `trace`; the trace is reduced to its queries and results, numbered Query 1..n, and
tool ids are dropped. Model: `REVIEWER_MODEL` (default claude-sonnet-5-5); key: `OLIST_AGENT_API_KEY`. Streaming,
retries and usage counting are imported from agent/analyst.py. There is no server-side refusal fallback, because a
fallback would answer with another model; a refusal is an agent failure (exit 1).

The reply must be exactly one JSON object `{"verdict", "reason", "findings": [{"check", "severity", "evidence",
"fix"}]}` with the brief's verdicts, check names and severities. Anything else (prose or a code fence around it, a
missing or extra key, an unknown check) gives `{"verdict": null, "error": "format_error"}` with exit 0. A verdict is
never guessed.

```
python evals/reviewer/run_reviewer_eval.py --label baseline
python evals/reviewer/run_reviewer_eval.py --label quick --only q013.ok,q013.wrong_filter
python evals/reviewer/run_reviewer_eval.py --rescore evals/reviewer/runs/detail/<run>.json   # no agent calls
python evals/reviewer/test_reviewer.py         # tests, fake client and fake agents only
```

The runner starts `--agent-cmd` (default `python agent/reviewer.py`) once per case, with a `--timeout` (default
300 s), and passes only the four fields above. The answer key stays in the runner.

| Metric | Counts a case when | Denominator |
|---|---|---|
| `verdict_match` | the verdict equals the expected one | all cases |
| `dangerous_miss` | approve on a case expected revise or reject | cases expected revise or reject |
| `false_alarm` | revise or reject on a case expected approve | cases expected approve |
| `soft_miss` | reject instead of revise, or revise instead of reject | cases expected revise or reject |
| `check_recall` | every expected check is among the flagged checks (extra flags cost nothing) | cases with expected checks |
| `evidence_valid` | every finding passes the evidence rule | cases with at least one finding |
| `invented_numbers` | a finding's evidence or fix has a number found in no source and not derived | cases with at least one finding |
| `derived_numbers` | a finding's evidence or fix has a number that is the sum or difference of two numbers from the response and trace | cases with at least one finding |
| `format_error`, `agent_failure` | the reviewer returned a format error, or crashed, timed out or printed no JSON object | all cases |

A format error or agent failure never counts as a dangerous miss or a false alarm; it fails `verdict_match` and
`check_recall`.

Scoring rules below are **scorer version 3** (`scorer_version` in every summary). History:

| Version | Summaries | Change and why |
|---|---|---|
| 1 | no `scorer_version` field (reviewer v1 runs of 2026-11-22) | First rule: double quotes and backticks only, whitespace and case normalised, ANSWER_FORMAT.md numbers counted as number sources. Reading the v1 failures showed most evidence failures were formatting (single quotes, ellipses, markdown), and that copying an ANSWER_FORMAT example number could never be caught. |
| 2 | 2026-11-24, replaced by v3 | Single-quoted spans (boundary rule), ellipsis pieces, stronger normalisation, derived vs invented numbers, ANSWER_FORMAT.md dropped as a number source. The last change made a reviewer quoting the spec's own example ("numbers like 37.42, not "37.42%"") count as inventing 37.42. |
| 3 | current | A number inside a quoted span of a finding that is found verbatim in a source (ANSWER_FORMAT.md included) is supported, and so is every occurrence of that value in the same finding. Outside such quotes ANSWER_FORMAT.md numbers stay excluded. |

**Normalisation.** Both the evidence and the sources are compared after: Unicode NFKD with accents dropped, curly
apostrophes mapped to `'`, straight and curly double quotes removed, `**`, `__` and backticks removed, lowercased,
whitespace collapsed.

**Evidence rule.** The sources are the response, each query's SQL and result, docs/metrics.md and
evals/ANSWER_FORMAT.md. A finding's evidence is valid when:

- (a) every query reference (`query N`, `queries N and M`, `Q N`) points to a query that exists (1 to the number of
  queries);
- (b) every quoted span occurs in a source. Quoted spans are `"..."`, `“...”`, `` `...` `` and single-quoted
  `'...'`, where the opening `'` must follow the start of the text, a space, `:`, `(` or `[`, and the closing `'`
  must precede a space, punctuation or the end (so the apostrophe in "Olist's" never opens a span). A span is split
  at `...`, `…` and `[...]`; every piece of 4 or more characters must occur, all in the same source. A span with no
  such piece is ignored;
- (c) there is an anchor: a valid query reference, a found quoted span, or the whole evidence (8 or more characters)
  found in a source.

The anchor rule is not loosened: an accurate paraphrase with no query reference and no quote fails, and so does a
quotation that is not in the inputs (for example "6,610 delivered orders" when the response says "with 6,610").

**Number rules.** Numbers are read from each finding's evidence and fix (`-?\d[\d,]*(\.\d+)?%?`, commas and `%`
dropped), leaving out query references and whole numbers from 0 to 10. Number sources are the response, the trace
(SQL and results) and docs/metrics.md; the illustrative values in evals/ANSWER_FORMAT.md are not sources. A number is
supported when a source number equals it or rounds to it at its own number of decimals (21.4 is supported by 21.41).
It is also supported when the same value occurs inside a quoted span of the same finding (evidence or fix) that is
found verbatim in a source, ANSWER_FORMAT.md included: a quotation of the inputs is not a made-up number. A quote
helps only its own finding, and a quote that is not verbatim in any source supports nothing.
An unsupported number is **derived** when it equals the sum or difference of two numbers from the response and trace
(for example 0.13 = 4400.0 − 4399.87), and **invented** otherwise. Derived numbers are reported separately from
invented ones: the brief says never to state a corrected number that is not in the trace, so they are worth seeing,
but they are not made up.

**Rescoring.** `--rescore DETAIL` re-scores the reviewer outputs saved in a detail file with the current scorer and the
current labels, without calling the agent. It writes a new summary and detail labelled `<label>-rescored`, with
`rescored_from`, `original_timestamp`, `original_git_sha` and `scorer_version`; files are opened in exclusive mode, so
an existing run is never overwritten.

Outputs: `evals/reviewer/runs/<UTC timestamp>_<label>.json` (committed: counts and rates as `{n, of, rate}`, tables
per fault type and per check, the expected × got confusion matrix, token totals, `agent_config` with the model from
the first case's USAGE line, git sha) and `evals/reviewer/runs/detail/<same name>.json` (gitignored: each case's
reviewer output, scores, stderr tail and usage). If every case fails before reaching the model (agent failure and zero
tokens), the runner prints a warning, saves nothing and exits 3.

## Brief v2

agent/REVIEWER_BRIEF.md v2 adds two things to section 4 (Output). The rest of the brief, the checks and the reviewer
code are unchanged.

- **Severity rubric.** High means the headline answer is wrong or unsupported: a wrong definition or population, a
  headline figure that differs from the query results beyond display rounding, an unjustified refusal, or a false
  premise accepted. Medium means the headline is right but a reader could be misled or cannot verify it: an
  unsupported claim (including a supporting figure that appears in no query result), a missing or contradicting
  assumption, or a format violation. Low means cosmetic. The revise rule now reads "no high findings, at least one
  medium finding". Why: v1 had severities with no definitions, so the same fault got different severities and
  verdicts across runs.
- **Evidence format.** Evidence is a query number, one short exact quote (about 20 words at most, no ellipses), or
  both. Quotes go in double quotes, or in backticks when they contain double quotes (JSON, SQL). Paraphrases are not
  allowed in evidence; explanations go in fix. Why: most v1 evidence failures were quotes the scorer could not match
  (single quotes, ellipses, quotes joined to commentary) or accurate paraphrases with nothing to check.

**Tuned on dev.** The rule that an empty assumptions list is a medium finding when the answer relies on a definition
was written after seeing the v1 miss on q013.missing_assumption. Both v1 runs approved it, although one of them
flagged `assumptions`. v2 is therefore tuned on these dev cases, so its dev score overstates how well it generalises.
The held-out run is the real test. The v1 summaries (`*_reviewer-v1-sonnet*.json`) stay in `runs/` as the baseline;
v2 runs use their own label (for example `reviewer-v2-sonnet`).

**Acceptance criteria for v2, decided before any v2 run:**

1. All 9 reject cases are still rejected, in both runs.
2. 0 false alarms on the 8 approve cases.
3. q013.missing_assumption now gets revise.
4. evidence_valid at least as high as the v1 rescored runs (21/23 and 24/25).
5. No new format errors.

If (1) or (2) fails, v2 is not adopted, and the cause is investigated before anything else is changed.

## Brief v2 outcome

Two v2 runs (`20261124T164129Z_reviewer-v2-sonnet-r1`, `20261124T164504Z_reviewer-v2-sonnet-r2`, claude-sonnet-5-5,
brief v2 at c068cb1, scorer v3). r2 records `git_dirty: true` only because r1's summary was untracked when it ran.

**Pre-registered criteria, as measured** (labels at run time: the q001.ok and q018.ok corrections):

| # | Criterion | r1 | r2 | Result |
|---|---|---|---|---|
| 1 | all 9 reject cases still rejected | 9/9 | 9/9 | met |
| 2 | 0 false alarms on the 8 approve cases | 1/8 (q010.ok) | 1/8 (q010.ok) | **failed as written** |
| 3 | q013.missing_assumption gets revise | revise | revise | met |
| 4 | evidence_valid at least as high as v1 rescored (21/23, 24/25) | 22/22 | 22/22 | met as a rate (100% against 91% and 96%); as a raw count, 22 is below 24 in the second pairing, because v2 made findings on fewer cases |
| 5 | no new format errors | 0/29 | 0/29 | met |

**Investigation of criterion 2.** The only false alarm is the same case in both runs: q010.ok got revise with one
medium `assumptions` finding quoting "Minimum volume of 5 eligible orders per state. Every state passes it". The trace
has one query, with no HAVING and LIMIT 8, so neither the threshold nor "every state passes it" is shown. The
finding is right and the label was wrong: q010.ok is corrected to revise (see Label corrections). v1 approved this case
in both runs.

**Decision.** v2 is adopted provisionally. By the stop rule, a failed criterion 2 means v2 is not adopted until the
cause is understood; the cause is a wrong label, not a reviewer error, so the rule's purpose is met. This judgement
was made after seeing the results and adds to the bias risk above. The held-out run is the real test.

**v1 against v2, both runs each** (scorer v3, rescored from the saved outputs; no new API calls).

Original labels (no corrections):

| metric | v1 r1 | v1 r2 | v2 r1 | v2 r2 |
|---|---|---|---|---|
| verdict_match | 25/29 | 26/29 | 26/29 | 26/29 |
| dangerous_miss | 2/19 | 1/19 | 0/19 | 0/19 |
| false_alarm | 2/10 | 2/10 | 3/10 | 3/10 |
| soft_miss | 0/19 | 0/19 | 0/19 | 0/19 |
| check_recall | 18/19 | 19/19 | 18/19 | 19/19 |
| evidence_valid | 21/23 | 24/25 | 22/22 | 22/22 |
| invented_numbers | 0/23 | 0/25 | 0/22 | 0/22 |
| derived_numbers | 0/23 | 1/25 | 0/22 | 0/22 |
| format_error | 0/29 | 0/29 | 0/29 | 0/29 |

Corrected labels (q001.ok, q010.ok, q018.ok):

| metric | v1 r1 | v1 r2 | v2 r1 | v2 r2 |
|---|---|---|---|---|
| verdict_match | 26/29 | 27/29 | 29/29 | 29/29 |
| dangerous_miss | 3/22 | 2/22 | 0/22 | 0/22 |
| false_alarm | 0/7 | 0/7 | 0/7 | 0/7 |
| soft_miss | 0/22 | 0/22 | 0/22 | 0/22 |
| check_recall | 20/22 | 21/22 | 20/22 | 21/22 |
| evidence_valid | 21/23 | 24/25 | 22/22 | 22/22 |
| invented_numbers | 0/23 | 0/25 | 0/22 | 0/22 |
| derived_numbers | 0/23 | 1/25 | 0/22 | 0/22 |
| format_error | 0/29 | 0/29 | 0/29 | 0/29 |

Under the original labels, v2 matches v1 on verdicts (26/29) and removes the dangerous misses (0 against 2 and 1)
at the cost of one more false alarm: q010.ok, the case later relabelled. Under the corrected labels both v2 runs match
every verdict. Read the corrected column with the bias risk in mind: all three corrections came from reviewer
findings.

## Held-out test

The dev cases above tuned the reviewer: two label rounds and brief v2 were all shaped by reading reviewer findings on
them. The held-out test measures the reviewer on answers it has never been tuned on: the analyst's saved answers to
the **hidden** questions.

**Design.**
- `heldout/build_heldout.py` reads the hidden analyst runs labelled v3-harder, abl-floor, grid-marts-bare and
  grid-raw-bare: the committed summary in evals/runs/ and its gitignored detail file. A run whose detail file is
  missing is skipped and named by label.
- Each hidden answer becomes a case with the same reviewer fields as the dev cases (question, answer_schema,
  response, trace) and an opaque id (h001, h002, …) assigned after a shuffle with a fixed seed.
- Answers graded format_error or incomplete_refusal are left out: there is no real answer to review.
- The cases (`heldout/cases.heldout.json`) and the key (`heldout/key.heldout.json`: source run, question id, grader
  outcome, manual-review flag, group) are written locally and gitignored. Only aggregates are printed.

**Labels.**
- An answer the grader marked wrong_confident or false_refusal is **expected reject**: its headline is wrong or
  withheld. Approving it is a dangerous miss.
- An answer the grader marked correct is **unlabelled**. A correct headline can still carry unsupported side claims,
  which is exactly what the dev label corrections kept finding, so revise is not an error there. It is reported as
  ambiguous. Reject on a grader-correct answer is reported as a clear false alarm.

**Running it.**
- `run_reviewer_eval.py --heldout --confirm-heldout --label <label>` refuses to run without the confirmation flag or
  on a dirty git tree.
- It sends the reviewer only the four reviewer fields; the opaque id and the key never reach it.
- It prints and commits aggregates only (`runs/<timestamp>_<label>_heldout.json`): counts and rates per group and per
  source label, distinct questions, evidence and number checks, tokens, model, scorer version, the sha256 of
  agent/REVIEWER_BRIEF.md and agent/reviewer.py, and the number of earlier held-out runs.
- Per-case reviewer outputs stay in the gitignored detail file. There is no option to show held-out cases, and
  held-out detail cannot be rescored.

**Rules.**
- One shot: run it once, with brief v2 and the reviewer as committed.
- No change to the brief or the reviewer is made because of the result.
- If anything is changed and the held-out run is repeated, the set is burned. `earlier_heldout_runs` records how many
  runs came before, and every later score must be read with that count.
- Nobody opens the held-out cases, key or detail while working on the reviewer.

**Caveats.**
- The wrong answers are not independent. The analyst gets the same few hidden questions wrong across runs, so several
  expected-reject cases share a question. Report counts together with `distinct_questions` (and the distinct
  questions among dangerous misses), not only rates.
- Labels come from the analyst grader, not from a human review of each answer. A grader-correct answer is unlabelled,
  and a grader-wrong answer is assumed to deserve reject.
- The reviewer was tuned on the dev cases only. The held-out score is the estimate that counts, and the dev scores
  overstate it.

Commands (from the repo root, with a clean tree):

```
python evals/reviewer/heldout/build_heldout.py
python evals/reviewer/run_reviewer_eval.py --heldout --confirm-heldout --label reviewer-v2-sonnet
python evals/reviewer/heldout/test_heldout.py     # tests, synthetic fixtures only
```
