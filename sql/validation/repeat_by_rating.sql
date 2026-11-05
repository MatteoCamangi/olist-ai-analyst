-- 180-day repeat rate by first-order review score. Cohort: first delivered order from 2017-01-01 to 2018-02-22
-- Expected: TOTAL 53792 customers, 1072 repeaters, 1.99%. Score 1: 5127, 91, 1.77; 2: 1693, 26, 1.54; 3: 4652, 93, 2.00;
-- 4: 10766, 198, 1.84; 5: 31156, 659, 2.12; no review: 398, 5, 1.26
WITH d AS (
    SELECT c.customer_unique_id, o.order_id, o.order_purchase_timestamp AS ts
    FROM raw.orders o
    JOIN raw.customers c USING (customer_id)
    WHERE o.order_status = 'delivered'
),
first_orders AS (
    SELECT customer_unique_id, order_id AS first_order_id, ts AS first_ts
    FROM d
    QUALIFY ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY ts, order_id) = 1
),
latest AS (
    SELECT order_id, review_score
    FROM raw.order_reviews
    QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_answer_timestamp DESC, review_id) = 1
),
cohort AS (
    SELECT f.customer_unique_id, l.review_score
    FROM first_orders f
    LEFT JOIN latest l ON l.order_id = f.first_order_id
    WHERE CAST(f.first_ts AS DATE) BETWEEN DATE '2017-01-01' AND DATE '2018-02-22'
),
returners AS (
    SELECT DISTINCT f.customer_unique_id
    FROM first_orders f
    JOIN d ON d.customer_unique_id = f.customer_unique_id
    WHERE date_diff('day', CAST(f.first_ts AS DATE), CAST(d.ts AS DATE)) BETWEEN 1 AND 180
),
by_rating AS (
    SELECT c.review_score,
           COUNT(*)                    AS customers,
           COUNT(r.customer_unique_id) AS repeaters
    FROM cohort c
    LEFT JOIN returners r USING (customer_unique_id)
    GROUP BY 1
)
SELECT COALESCE(CAST(review_score AS VARCHAR), 'no review') AS review_score,
       customers, repeaters,
       ROUND(100.0 * repeaters / customers, 2) AS repeat_rate_pct,
       1 AS sort_key
FROM by_rating
UNION ALL
SELECT 'TOTAL', SUM(customers), SUM(repeaters),
       ROUND(100.0 * SUM(repeaters) / SUM(customers), 2), 2
FROM by_rating
ORDER BY sort_key, review_score;