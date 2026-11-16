/*
Q10: Which customer state has the highest late delivery rate? (delivered orders only)
*/

WITH late_deliveries_cte AS (
    SELECT 
        order_id,
        customer_id,
        order_delivered_customer_date::DATE > order_estimated_delivery_date::DATE AS is_late
    FROM raw.orders
    WHERE order_status = 'delivered' 
    AND order_delivered_customer_date IS NOT NULL
)

SELECT
    c.customer_state,
    ROUND(100.0 * COUNT(*) FILTER(WHERE ld.is_late) / COUNT(*),2) AS late_rate_pct
FROM raw.customers c 
INNER JOIN late_deliveries_cte ld 
    ON c.customer_id = ld.customer_id
GROUP BY c.customer_state
ORDER BY 2 DESC 
LIMIT 1