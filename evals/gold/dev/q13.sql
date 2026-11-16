/*
Q13: How many orders were canceled, and what share of all orders is that?
*/

SELECT
    COUNT(*) FILTER (WHERE order_status = 'canceled') AS canceled_orders_cnt,
    ROUND(100.0 * COUNT(*) FILTER (WHERE order_status = 'canceled') / COUNT(*),2) AS cancel_order_rate_pct
FROM raw.orders