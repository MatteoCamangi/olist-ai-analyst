/*
Q4: List the sellers at or above the 95th percentile of delivered revenue, and those at or below the 25th.
*/

WITH sellers_revenue_cte AS (
    SELECT
        oi.seller_id,
        SUM(oi.price) AS seller_revenue
    FROM raw.order_items oi 
    INNER JOIN raw.orders o 
        ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY 1
)

, percentiles_cte AS (
    SELECT 
        PERCENTILE_CONT(0.95) WITHIN GROUP(ORDER BY seller_revenue) as p95,
        PERCENTILE_CONT(0.25) WITHIN GROUP(ORDER BY seller_revenue) as p25
    FROM sellers_revenue_cte
)

SELECT 
    seller_id,
    seller_revenue,
    CASE WHEN seller_revenue >= (SELECT p95 FROM percentiles_cte) THEN 'p95'
         ELSE 'p25'
    END AS percentile_bucket
FROM sellers_revenue_cte
WHERE (seller_revenue >= (SELECT p95 FROM percentiles_cte)
OR seller_revenue <= (SELECT p25 FROM percentiles_cte))