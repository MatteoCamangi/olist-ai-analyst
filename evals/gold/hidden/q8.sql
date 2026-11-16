/*
Q8: What is the average delivery time in days (purchase to customer delivery) and 
the count of late deliveries (delivered after the estimated date)? Delivered orders only.
*/

WITH delivery_time_cte AS (
    SELECT 
        order_id,
        (order_delivered_customer_date::DATE - order_purchase_timestamp::DATE) AS delivery_days,
        order_delivered_customer_date::DATE > order_estimated_delivery_date::DATE AS is_late
    FROM raw.orders
    WHERE order_status = 'delivered'
    AND order_delivered_customer_date IS NOT NULL
)

SELECT 
    ROUND(AVG(delivery_days),1) AS delivery_days_avg,
    COUNT(*) AS orders_delivered_cnt,
    COUNT(*) FILTER (WHERE is_late)  AS orders_delivered_late_cnt,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_late) / COUNT(*), 2) AS late_rate_pct
FROM delivery_time_cte