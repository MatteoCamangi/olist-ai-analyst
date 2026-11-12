# Olist AI analyst

## Goal
Build and evaluate AI analyst agents on the Olist e-commerce dataset (DuckDB + dbt). Stages: dbt models, metric docs, a question set with a grader (evals), an analyst agent, a reviewer agent, a presenter and dashboard.

## Workflow
- Models, YAML and docs are drafted by the maintainer. The agent reviews them, runs checks and prepares commits.
- Report problems with file, line and reason, and propose fixes in the message. Edit a file only when the file and the change are named.
- The agent may run dbt, the validation queries and git on feature branches, and may open pull requests with `gh` if available. The maintainer merges.
- Never push to main. Never modify data/raw or the raw schema, and never delete warehouse.duckdb.

## Environment
- Activate with `source .venv/bin/activate`. Run dbt from dbt/. dbt Core 1.12 with the duckdb adapter (staying on v1).
- DuckDB allows one writer. On a lock error run `pkill -f ipykernel_launcher`.
- Checks from the repo root: `python sql.py "<sql>"` or `python sql.py sql/validation/<file>.sql`.
- Use `dbt build` (runs models and tests in order). Use `--select` for the models being changed.

## Source of truth
- docs/metrics.md: definitions, rules and validated numbers.
- docs/data_notes.md: facts and traps about the data.
- sql/validation/: queries that reproduce each validated number, with expected values in the header.
- If a model disagrees with a validated number, the model is wrong until proven otherwise.

## Conventions
- Layers: raw -> staging (1:1 views, no logic) -> intermediate (shared rules and grain reductions, never queried directly) -> marts (the only layer users and agents query).
- Every model has a "one row per ___" sentence in its description and a test that proves it.
- Test where the risk is introduced. Do not repeat tests already in an upstream layer. not_null only where a metric depends on the column.
- Composite keys use dbt_utils.generate_surrogate_key, with not_null on the component columns.
- Start from the grain table and left join. Reduce many-side tables (items, payments, reviews) to one row per order before joining. Never join two many-side tables.
- Document marts fully. Staging gets a model description and inline text only where a column has a trap. A doc block is defined once, in the docs file of the first layer that documents the column. Block names are global, so later layers reference them with doc(). Names in SQL, YAML, doc() and ref() must match exactly.
- One commit per unit of work, with its YAML, docs and tests. Merge commits, not squash.

## Decisions
- "Orders" means all orders placed. Revenue, delivery, review and customer metrics use delivered orders only. Shipped orders are not delivered.
- As-of date 2018-08-21 (dbt var as_of_date). Trend window Jan 2017 to Jul 2018. Never use the current date.
- Wide facts: status and customer location are copied onto the facts. Neither fact depends on the other: both read int_orders_enriched.
- int_orders_latest_reviews has one row per order that has a review. Orders without a review are absent, so left join it and keep a null score (never 0).
- dim_products holds the category rule: official translation, then a manual mapping for two names, then unknown.
- dim_customers is one row per person, delivered orders only, with no location because 39 people appear in several states. Repeat flags: overall (a delivered order on a later calendar day) and 180-day cohort (null when the first order is after 2018-02-22).
- Payments are out of scope for now. A future fct_order_payments is one row per payment.

## Planned facts
- fct_orders: one row per order (99,441). No seller and no payments. Columns: order_id, customer_id, customer_unique_id, order_purchase_timestamp/day/week/month, order_delivered_customer_date, order_estimated_delivery_date, order_status, order_status_group, is_delivered, delivery_days and is_late (null unless delivered with a delivery date), customer_city, customer_state, items_count, sellers_count, order_price, order_freight_value, review_id, review_score.
- fct_order_items: one row per item line (112,650), key order_item_key. Order-level columns come from int_orders_enriched. Adds product_id, seller_id, product_category_name_en (from dim_products), seller_state (from stg_sellers), item_price, item_freight_value. No review score and no late flag (they would count once per item).
- Tie-outs: fct_orders reproduces 99,441 rows, 96,478 delivered, late rate 6.77% over 96,470 eligible orders, 95,832 delivered orders with a score averaging 4.156, average delivery time 12.5 days. fct_order_items has 112,650 rows. Delivered revenue is 13,221,498.11 in both, and the two must agree.

## Status
- Merged to main: staging (8 views, 41 tests), int_orders_enriched, int_orders_latest_reviews, dim_products, dim_customers.
- Next: fct_orders, fct_order_items, then the question set and grader.