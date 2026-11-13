# Metric definitions

How to use this file: Part 1 rules apply to every answer. Part 2 conventions are shared rules that the metrics refer to. Part 3 metrics use the same fields. 'Check (raw tables)' is the independent validation query. 'Query (marts)' is added only where the question has a trap. 'Reproduced on marts' means the mart query gave the same number as the raw-table check. "n/a" means not applicable. SQL is inline when short, otherwise it is a file under sql/validation/.
Evidence levels for validated values: "confirmed" means reproduced by the validation query. "confirmed, independently recomputed" means a second query written differently, reading the CSV files directly, gave the same number (sql/validation/independent_checks.sql)

## Part 1: Global rules
- Each metric states its own population. "Orders" without a qualifier means all orders placed, whatever the status
- Metrics on revenue, delivery, reviews, customers, sellers and products use delivered orders only. If the question names another status (canceled, shipped and so on), filter by that status. Never silently restrict to delivered
- Every answer states the population and the exact date range it used, for example "99,441 orders placed, of which 96,478 delivered"
- Never use the current date. The as-of date is 2018-08-21
- Revenue is gross. No refund, return, cost or commission data exists, so profit cannot be computed: say so
- Never join order_payments or order_reviews to orders or items without reducing them to one row per order first
- Data covers 2016-09-04 to 2018-10-17. Outside that range there is no data: say so and never report it as zero. 2016 and Aug to Oct 2018 are sparse or partial: answer with a warning
- Comparisons between groups show association, not causation: say so
- Use column names exactly as in the source, including product_name_lenght and product_description_lenght (misspelled in the data). The status string is 'canceled' with one l
- Answer questions from the marts (fct_orders, fct_order_items, fct_order_sellers, dim_customers, dim_products). Lines labelled 'Check (raw tables)' are validation queries on the raw tables, not queries to copy

## Part 2: Conventions

### Status groups
- delivered 96,478. In flight 1,729 (shipped 1,107, invoiced 314, processing 301, created 5, approved 2). Canceled 625. Unavailable 609
- order_status is the status at extraction time, with no history. A question about the status "during" a period can only be answered as orders purchased in that period with that current status
- Shipped orders are not delivered. They never reached the delivered status, and some may have been delivered in reality

### Time and trends
- Month and week of an order come from order_purchase_timestamp
- Trend window: Jan 2017 to Jul 2018 (19 months). Aug 2018 is partial (daily volume falls away after 2018-08-21). Flag it in any monthly comparison that includes August
- Weeks run Monday to Sunday. Only complete weeks are reported (Sunday on or before the as-of date): 2017-01-02 to 2018-08-19, 85 weeks
- Relative windows: "last N days" means the N days ending on 2018-08-21, inclusive (last 7 days = 2018-08-15 to 2018-08-21). "Last month" means 2018-07
- Change = (current period − previous period) / previous period, as a percentage. The previous period must also be complete. The first period has no change value
- Flag Black Friday when it is in a comparison (2017-11-24: 1,176 orders; November 2017 is a seasonal spike)
- Flag growth in Jan to Mar 2017 as marketplace ramp-up, not underlying growth
- Weekly changes are noisy: about half of weeks move more than 10%. Never describe a single week's change as a trend. Known swings: week of 2017-11-20 revenue +131.4% (Black Friday); week of 2018-05-21 orders −47.9% and revenue −48.1% (cause not in the data); week of 2017-08-28 revenue +70.0% on orders +13.6%

### Geography
- Location means customer location. Seller location is used only when the question says seller
- Region and state both mean customer_state (27 values). Country is always Brazil: answer country questions with a note that the data only covers Brazil
- City means the pair customer_city + customer_state, never the name alone (the same name exists in several states). Names are lowercase and unaccented ('sao paulo'). Show city and state together
- Customers link to orders one-to-one. Do not use the geolocation table
- Counting rows in customers counts order-level records, not people. For people use distinct customer_unique_id

### Reviews
- The review of an order is its latest review (by review_answer_timestamp, ties broken by review_id)
- Orders without a review have no score: 768 orders, 646 of them delivered. Report how many were excluded

### Categories and products
- No quantity column: an item row is one unit sold, so volume = item rows
- Always show the English category next to product_id (no product names exist). Missing category = 'unknown'. Two untranslated names are mapped manually (portateis_cozinha_e_preparadores_de_alimentos and pc_gamer, proposed English names portable_kitchen_and_food_preparers and pc_gamer)
- Shares use total revenue as the denominator, including 'unknown'
- Category names come from the translation table with source typos (home_confort, costruction_tools_garden, costruction_tools_tools, fashio_female_clothing, arts_and_craftmanship). Use them exactly as written. Do not fix or merge similar names (home_confort and home_comfort_2, home_appliances and home_appliances_2, construction_tools_* and costruction_tools_*) unless the question asks

### Sellers
- 1,278 of 98,666 orders with items (1.30%) contain items from more than one seller. Seller revenue can use item rows or marts.fct_order_sellers. Seller order counts, ratings and late rates use marts.fct_order_sellers (one row per order and seller), which makes count(*) the order count and attaches the order's review and delivery to each seller
- Delivery and review belong to the order. In a multi-seller order they count for every seller in it, so a seller can look late or poorly rated because of another seller
- Seller order counts are never added up (they sum to 97,819 against 96,478 delivered orders)
- Default for "top sellers": by revenue, top 10. The answer states which was used
- Ranking by rating requires at least 5 reviewed delivered orders (1,761 of 2,970 sellers with a delivered order qualify). Show each seller's reviewed-order count next to its rating. Ties are broken by reviewed orders (more first), then revenue
- Show seller_id (an anonymized hash) with seller_state

## Part 3: Metrics

### Orders placed
- Definition: number of orders placed, all statuses
- Population: all orders
- Grain and source: one row per order, raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT COUNT(*) FROM raw.orders
- Common mistakes: restricting to delivered when the question says "orders"; counting order_items rows (those are items)
- Caveats: 775 orders have no items (mostly unavailable and canceled), so they carry no value
- Validated value: 99,441 (confirmed)

### Delivered orders
- Definition: number of orders with status 'delivered'
- Population: delivered orders
- Grain and source: one row per order, raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT COUNT(*) FROM raw.orders WHERE order_status = 'delivered'
- Common mistakes: counting shipped orders as delivered
- Caveats: 1,107 shipped orders are not counted
- Validated value: 96,478 (confirmed)

### Cancellation rate
- Definition: canceled orders divided by orders placed
- Population: all orders
- Grain and source: one row per order, raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*), 2) FROM raw.orders
- Common mistakes: using delivered orders as the denominator; adding unavailable orders (609) to canceled without saying so
- Caveats: 164 canceled orders have no items
- Validated value: 0.63% (625 of 99,441, confirmed)

### Revenue (gross merchandise value, item price only)
- Definition: sum of item price, excluding freight
- Population: delivered orders
- Grain and source: item rows in raw.order_items joined to raw.orders on order_id. Mart: marts.fct_orders
- Check (raw tables): SELECT ROUND(SUM(i.price), 2) FROM raw.order_items i JOIN raw.orders o USING (order_id) WHERE o.order_status = 'delivered'
- Common mistakes: joining order_payments before summing (4.5% too high); using payment_value (includes freight); including canceled or shipped orders
- Caveats: gross, no refunds. This is what customers paid sellers, not Olist's income. Shipped orders (150,727.44) are excluded. Freight (2,251,909.54 across all statuses) is a separate amount
- Validated value: 13,221,498.11 (confirmed)

### Average order value
- Definition: revenue divided by delivered orders
- Population: delivered orders
- Grain and source: item rows in raw.order_items joined to raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT ROUND(SUM(i.price) / COUNT(DISTINCT o.order_id), 2) FROM raw.order_items i JOIN raw.orders o USING (order_id) WHERE o.order_status = 'delivered'
- Common mistakes: dividing by item rows or by all 99,441 orders placed
- Caveats: n/a. All 96,478 delivered orders have items
- Validated value: 137.04 (confirmed)

### Late delivery rate
- Definition: share of delivered orders whose delivery date falls on a later calendar day than the estimated delivery date. One day after the estimate is already late
- Population: delivered orders with a delivery date (96,470)
- Grain and source: one row per order, raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE CAST(order_delivered_customer_date AS DATE) > CAST(order_estimated_delivery_date AS DATE)) / COUNT(*), 2) FROM raw.orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
- Query (marts): SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE is_late) / COUNT(is_late), 2) FROM marts.fct_orders
- Common mistakes: comparing timestamps (8.11% instead of 6.77%, because estimates are stored at midnight); including orders without a delivery date
- Caveats: orders still in transit are excluded, so the rate may understate lateness. 8 delivered orders have no delivery date
- Validated value: 6.77% (6,534 of 96,470, confirmed). Reproduced on marts

### Delivery time
- Definition: average calendar days from purchase to delivery to the customer
- Population: delivered orders with a delivery date (96,470)
- Grain and source: one row per order, raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT ROUND(AVG(date_diff('day', order_purchase_timestamp, order_delivered_customer_date)), 2) FROM raw.orders WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
- Common mistakes: including orders without a delivery date; treating the result as 24-hour periods (date_diff counts day boundaries)
- Caveats: average only, no median computed
- Validated value: 12.5 days (confirmed)

### Review score
- Definition: score of the latest review of an order. The average is taken over reviewed orders
- Population: delivered orders with a review (95,832)
- Grain and source: one row per order after taking the latest review, raw.order_reviews joined to raw.orders. Mart: marts.fct_orders
- Check (raw tables): SELECT r.order_id, r.review_score FROM raw.order_reviews r JOIN raw.orders o USING (order_id) WHERE o.order_status = 'delivered' QUALIFY ROW_NUMBER() OVER (PARTITION BY r.order_id ORDER BY r.review_answer_timestamp DESC, r.review_id) = 1
- Common mistakes: joining raw reviews to orders (duplicates 547 orders); averaging as if every order had a review; including undelivered orders
- Caveats: 646 delivered orders have no review. Undelivered orders score much lower (all statuses: 98,673 reviewed orders, average 4.086)
- Validated value: 95,832 reviewed delivered orders, average 4.156 (confirmed)

### Customers
- Definition: distinct customer_unique_id with at least one delivered order
- Population: delivered orders
- Grain and source: person (customer_unique_id), raw.orders joined to raw.customers. Mart: marts.dim_customers
- Check (raw tables): SELECT COUNT(DISTINCT c.customer_unique_id) FROM raw.orders o JOIN raw.customers c USING (customer_id) WHERE o.order_status = 'delivered'
- Common mistakes: counting distinct customer_id (that counts orders); counting rows in customers
- Caveats: n/a
- Validated value: 93,358 (confirmed)

### Repeat customer rate
- Definition: share of customers with a delivered order placed at least 1 calendar day after the date of their first delivered order
- Population: customers (93,358)
- Grain and source: person, raw.orders joined to raw.customers. Mart: marts.dim_customers
- Check (raw tables): file sql/validation/repeat_rate.sql
- Query (marts): SELECT COUNT(*) FILTER (WHERE is_repeat_customer) AS repeaters, ROUND(100.0 * COUNT(*) FILTER (WHERE is_repeat_customer) / COUNT(*), 2) AS repeat_rate_pct FROM marts.dim_customers
- Common mistakes: counting same-day extra orders as repeat (3.00% instead of 2.16%); counting customer_id
- Caveats: the data covers about two years, so the rate is a floor on lifetime repeat behavior. 829 customers placed their second order the same day (probably one basket split in several orders)
- Validated value: 2,015 of 93,358 = 2.16% (confirmed). 2,801 customers have a second delivered order, but 786 of them only placed same-day extra orders. Timing of the second order: same day 829, 1 to 30 days 587, 31 to 90 days 492, 91 to 180 days 413, over 180 days 480. Reproduced on marts

### Revenue by category
- Definition: revenue grouped by English category name
- Population: delivered orders
- Grain and source: item rows in raw.order_items joined to raw.orders, raw.products and the translation table (left join). Mart: marts.fct_order_items
- Check (raw tables): file sql/validation/revenue_by_category.sql
- Common mistakes: inner-joining the translation table (drops 1.33% of revenue); showing Portuguese names; dividing shares by categorized revenue only
- Caveats: 'unknown' (no category) is 170,726.63 (1.29%). Two untranslated names are mapped manually
- Validated value: total 13,221,498.11 (confirmed through the products join, 110,197 item rows). Top categories: health_beauty 1,233,131.72; watches_gifts 1,166,176.98; bed_bath_table 1,023,434.76; sports_leisure 954,852.55; computers_accessories 888,724.61

### Delivered orders and revenue by state or city
- Definition: delivered orders and revenue grouped by customer_state, or by customer_city + customer_state
- Population: delivered orders
- Grain and source: delivered orders and item rows, raw.orders joined to raw.customers and raw.order_items. Mart: marts.fct_orders
- Check (raw tables): files sql/validation/revenue_by_state.sql and sql/validation/revenue_by_city.sql
- Common mistakes: grouping by city name without state; counting rows in customers (order records, not people); using the geolocation table; answering "by country" with more than one value
- Caveats: customer location only (see Geography)
- Validated value (confirmed): states tie to 96,478 orders and 13,221,498.11. SP 40,501 and 5,067,633.16; RJ 12,350 and 1,759,651.13; MG 11,354 and 1,552,481.83. Cities: sao paulo SP 15,045 and 1,859,556.83 (14.1% of revenue); rio de janeiro RJ 6,601 and 955,573.97; belo horizonte MG 2,697 and 346,039.04; brasilia DF 2,071 and 295,814.72; curitiba PR 1,489 and 205,999.33. The top 10 cities total 4,458,132.81 (33.7%)

### Weekly and monthly trends
- Definition: delivered orders and revenue by month or Monday to Sunday week of purchase, with percent change against the previous complete period
- Population: delivered orders, complete periods inside the window
- Grain and source: delivered orders (orders) and item rows (revenue), raw.orders joined to raw.order_items. Mart: marts.fct_orders
- Check (raw tables): files sql/validation/trends_monthly.sql and sql/validation/trends_weekly.sql
- Common mistakes: including partial August 2018, 2016 or partial weeks; presenting Feb 2017 or Nov 2017 changes as growth; using the current date
- Caveats: see Time and trends (ramp-up and Black Friday flags)
- Validated value (confirmed): 19 months and 85 weeks. Jan 2017 750 delivered orders and 111,798.36; Feb 2017 1,653 and 234,223.40 (+109.5% revenue); Nov 2017 7,289 and 987,765.37 (+52.4% revenue, +62.8% orders vs Oct); Jul 2018 6,159 and 867,953.46. Week of 2018-08-13: 1,839 orders and 243,458.02 (−9.9% revenue vs the previous week)

### Seller revenue and delivered orders
- Definition: per seller_id, revenue = sum of item price and delivered orders = distinct delivered orders containing the seller's items
- Population: delivered orders
- Grain and source: item rows in raw.order_items joined to raw.orders and raw.sellers. Mart: marts.fct_order_sellers
- Check (raw tables): file sql/validation/top_sellers.sql
- Query (marts): SELECT seller_id, seller_state, COUNT(*) AS delivered_orders, ROUND(SUM(items_price), 2) AS revenue FROM marts.fct_order_sellers WHERE is_delivered GROUP BY 1, 2 ORDER BY 4 DESC LIMIT 10
- Common mistakes: adding up seller order counts; counting orders at order level and losing multi-seller orders
- Caveats: see Sellers (item level, multi-seller orders, default top 10 by revenue). Revenue can also be computed from marts.fct_order_items; delivered orders per seller use fct_order_sellers (one row per order and seller, so count(*) is the order count)
- Validated value (confirmed): seller revenue sums to 13,221,498.11 and seller order counts to 97,819. Top seller 4869f7a5dfa277a7dca6462dcf3b52b2 (SP): 1,124 delivered orders and 226,987.93. The top 10 total 1,754,800.00 (13.3% of revenue), 9 of them in SP. Reproduced on marts

### Seller average rating
- Definition: average of the latest review score per order, over the seller's delivered orders that have a review
- Population: sellers with at least 5 reviewed delivered orders (1,761)
- Grain and source: seller and order (distinct), raw.order_items, raw.orders and the latest review per order. Mart: marts.fct_order_sellers
- Check (raw tables): file sql/validation/seller_rating.sql
- Query (marts): SELECT seller_id, seller_state, COUNT(review_score) AS reviewed_orders, ROUND(AVG(review_score), 2) AS avg_rating FROM marts.fct_order_sellers WHERE is_delivered GROUP BY 1, 2 HAVING COUNT(review_score) >= 5 ORDER BY avg_rating DESC, reviewed_orders DESC, SUM(items_price) DESC, seller_id LIMIT 10
- Common mistakes: ranking sellers with very few reviews; joining raw reviews (duplicates orders); ignoring ties
- Caveats: in a multi-seller order the same review counts for every seller in it. The ten best sellers all average 5.0, so ties exist
- Validated value (confirmed): 1,761 qualifying sellers. Top 10 by rating all 5.0 with 33, 12, 9, 8, 8, 8, 8, 7, 7, 7 reviewed orders. Reproduced on marts

### Seller late delivery rate
- Definition: per seller, the share of their delivered orders with a delivery date whose delivery fell on a later calendar day than the estimated delivery date
- Population: delivered orders with a delivery date, per seller. Sellers with fewer than 5 such orders are not ranked
- Grain and source: one row per order and seller, marts.fct_order_sellers
- Check (raw tables): TODO (no raw-table check exists yet)
- Query (marts): SELECT seller_id, seller_state, COUNT(is_late) AS eligible_orders, ROUND(100.0 * COUNT(*) FILTER (WHERE is_late) / COUNT(is_late), 2) AS late_rate_pct FROM marts.fct_order_sellers WHERE is_delivered GROUP BY 1, 2 HAVING COUNT(is_late) >= 5 ORDER BY late_rate_pct DESC, eligible_orders DESC, seller_id LIMIT 10
- Common mistakes: counting an order once per item instead of once per seller; adding up seller orders to get total orders; reading the rate as seller performance
- Caveats: delivery belongs to the order, so in a multi-seller order it counts for every seller in it. Small sellers have noisy rates. Same exclusions as the order-level late rate
- Validated value: TODO

### Seller average delivery time
- Definition: per seller, the average calendar days from purchase to delivery over their delivered orders with a delivery date
- Population: delivered orders with a delivery date, per seller. Sellers with fewer than 5 such orders are not ranked
- Grain and source: one row per order and seller, marts.fct_order_sellers
- Check (raw tables): TODO (no raw-table check exists yet)
- Query (marts): SELECT seller_id, seller_state, COUNT(delivery_days) AS eligible_orders, ROUND(AVG(delivery_days), 1) AS avg_delivery_days FROM marts.fct_order_sellers WHERE is_delivered GROUP BY 1, 2 HAVING COUNT(delivery_days) >= 5 ORDER BY avg_delivery_days DESC, eligible_orders DESC, seller_id LIMIT 10
- Common mistakes: counting an order once per item instead of once per seller; adding up seller orders to get total orders; reading the average as seller performance
- Caveats: delivery belongs to the order, so in a multi-seller order it counts for every seller in it. Small sellers have noisy averages. Same exclusions as the order-level delivery time
- Validated value: TODO

### Product volume and revenue
- Definition: volume = number of item rows, revenue = sum of item price, per product_id. "Best-selling" means by volume unless the question says revenue
- Population: delivered orders
- Grain and source: item rows in raw.order_items joined to raw.orders, raw.products and the translation table. Mart: marts.fct_order_items
- Check (raw tables): file sql/validation/top_products.sql
- Common mistakes: looking for a quantity column; showing product_id without its category; ranking by volume when asked for revenue
- Caveats: rankings by volume and by revenue differ, so say which one was used. No product names exist
- Validated value (confirmed): by revenue, bb50f2e236e5eea0100680137654686c (health_beauty) 194 item rows and 63,560.00; 6cdd53843498f92890544667809f1595 (health_beauty) 153 and 53,652.30; d6160fb7873f184099d9bc95e30376af (computers) 33 and 45,949.35 (volume rank 315). By volume, aca2eb7d00ea1a7b8ebd4e68314663af (furniture_decor) 520 rows and 37,104.30 (revenue rank 10); 422879e10f46682990de24d770e7f83d (garden_tools) 484 and 26,577.22; 99a4788cb24856965c36a24e339b6058 (bed_bath_table) 477 and 42,049.66. Only 5 of the top 10 by volume are also in the top 10 by revenue