-- Repeat customer rate: delivered order at least 1 calendar day after the first delivered order
-- Expected: customers 93358, repeat_customers 2015, repeat_rate_pct 2.16
WITH o AS (
    SELECT c.customer_unique_id,
           CAST(o.order_purchase_timestamp AS DATE) AS d,
           MIN(CAST(o.order_purchase_timestamp AS DATE)) OVER (PARTITION BY c.customer_unique_id) AS first_d
    FROM raw.orders o
    JOIN raw.customers c USING (customer_id)
    WHERE o.order_status = 'delivered'
)
SELECT COUNT(DISTINCT customer_unique_id) AS customers,
       COUNT(DISTINCT customer_unique_id) FILTER (WHERE d > first_d) AS repeat_customers,
       ROUND(100.0 * COUNT(DISTINCT customer_unique_id) FILTER (WHERE d > first_d)
             / COUNT(DISTINCT customer_unique_id), 2) AS repeat_rate_pct
FROM o;