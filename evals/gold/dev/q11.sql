/*
Q11: Which seller has the highest late delivery rate, among sellers with at least 10 delivered orders? 
(An order counts for a seller if it includes at least one of their items. Delivered orders only.)
*/


WITH late_deliveries_cte AS (
    SELECT 
        order_id,
        order_delivered_customer_date::DATE > order_estimated_delivery_date::DATE AS is_late
    FROM raw.orders
    WHERE order_status = 'delivered' 
    AND order_delivered_customer_date IS NOT NULL
), 

seller_orders_cte AS (
    SELECT DISTINCT
        seller_id,
        order_id
    FROM raw.order_items
)

SELECT
    so.seller_id,
    COUNT(*) AS delivered_orders,
    COUNT(*) FILTER (WHERE ld.is_late) AS late_orders,
    ROUND(100.0 * COUNT(*) FILTER (WHERE ld.is_late) / COUNT(*), 2) AS late_rate_pct
FROM seller_orders_cte so
INNER JOIN late_deliveries_cte ld 
    ON so.order_id = ld.order_id
GROUP BY so.seller_id
HAVING COUNT(*) >= 10
ORDER BY late_rate_pct DESC 
LIMIT 1
