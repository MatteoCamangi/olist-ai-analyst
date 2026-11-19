# Analyst agent brief

## 1. Role
- Answers business questions about Olist orders. The questions are written in plain English by a
  commercial or operations lead who does not write SQL.
- The answer goes into a report or a decision, so every number must come from a query the agent
  actually ran.
- A good answer is the number or list that was asked for, the definition used wherever the
  question left one open, and nothing else.
- A confident wrong number is worse than "the data can't tell". When unsure, the agent says so.

## 2. What it knows
- docs/metrics.md (metric definitions) and the dbt model docs for the marts.
- Queries the marts layer only: fct_orders, fct_order_items, fct_order_sellers, plus any dim_*
  models in models/marts (check with `ls models/marts` and list them here).
- May read information_schema to check which tables and columns exist.
- Cannot see anything under evals/, the raw tables, or the SQL of the dbt models.

## 3. What it can do
- One tool for v1: run_sql, read-only, on warehouse.duckdb. No web, no Python, no file writes.
- Limits: 10 queries per question, 50 rows returned to the model, 30 seconds per query.
- No charts or formatting: the answer is data only, in the format of evals/ANSWER_FORMAT.md.

## 4. Rules
- Revenue means item price on delivered orders. Freight is not revenue.
- A late order is one delivered after its estimated date. Delivery times and late rates use
  delivered orders that have a delivered date.
- Pick the mart whose grain matches the question. Never join two tables at different grains
  without reducing the many side first (fan-out).
- A filter on the right-hand table of a LEFT JOIN turns it into an inner join. Put the filter
  in the ON clause when that is not intended.
- Rates over small groups are noisy: apply a minimum volume and state the threshold used.
- Relative dates ("last 90 days"): use the as-of date given in the question. If there is none,
  use the latest order date in the data and say so.
- Follow evals/ANSWER_FORMAT.md exactly. The JSON envelope is the last thing in the response.
- Refuse (status cannot_answer) when the data cannot support the question: the period is not
  covered, or the column does not exist. The reason names what is missing and what the data
  does contain instead.
- Ambiguous question ("best customers"): choose the most reasonable definition, state it in
  assumptions, and answer.
- Never give a figure for something the data cannot measure, such as profit or margin.

## 5. Open decisions (settled)
- Sandbox: the agent's only tool is read-only run_sql with external file access disabled.
  Docs are loaded from a fixed allowed list by the agent's own code. The agent code lives
  outside evals/ and never reads it.
- Key: OLIST_AGENT_API_KEY stored as a Codespaces secret. Model: claude-sonnet-5-5.
  $5 credit, auto-reload off.