/*
Q6: What is the repeat rate (customers with a second order / customers with at least one order)? 
And the 180-day repeat rate (second order within 180 days of the first)? (customer_unique_id)
*/

WITH params AS (
    SELECT DATE '2018-08-21' AS as_of_date
),

order_days AS (
    SELECT DISTINCT
        c.customer_unique_id,
        CAST(o.order_purchase_timestamp AS DATE) AS order_day
    FROM raw.orders o
    JOIN raw.customers c ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
),

ranked AS (
    SELECT
        customer_unique_id,
        order_day,
        ROW_NUMBER() OVER (PARTITION BY customer_unique_id ORDER BY order_day) AS rn
    FROM order_days
),

per_customer AS (
    SELECT
        customer_unique_id,
        MIN(order_day) FILTER (WHERE rn = 1) AS first_day,
        MIN(order_day) FILTER (WHERE rn = 2) AS second_day
    FROM ranked
    GROUP BY customer_unique_id
)

SELECT
    COUNT(*)                                                         AS customers,
    COUNT(second_day)                                                AS repeat_customers,
    ROUND(100.0 * COUNT(second_day) / COUNT(*), 2)                   AS repeat_rate_pct,

    COUNT(*) FILTER (WHERE first_day <= p.as_of_date - 180)          AS eligible_180d,
    COUNT(*) FILTER (WHERE first_day <= p.as_of_date - 180
                       AND second_day - first_day <= 180)            AS repeat_180d,
    ROUND(100.0
        * COUNT(*) FILTER (WHERE first_day <= p.as_of_date - 180
                             AND second_day - first_day <= 180)
        / NULLIF(COUNT(*) FILTER (WHERE first_day <= p.as_of_date - 180), 0)
    , 2)                                                             AS repeat_rate_180d_pct
FROM per_customer
CROSS JOIN params p;