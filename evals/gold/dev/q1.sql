/*
Q1: How many orders did Olist receive in 2017 (by purchase date)? How many of them were delivered? 
--What was the total revenue of the delivered ones? 
*/

WITH revenue_cte AS (
    SELECT 
        order_id,
        SUM(price) AS order_revenue 
    FROM raw.order_items
    GROUP BY order_id
)

SELECT 
    COUNT(*) AS orders_cnt,
    COUNT(*) FILTER (WHERE O.order_status = 'delivered') AS delivered_orders_cnt,
    SUM(r.order_revenue) FILTER (WHERE o.order_status = 'delivered') AS total_revenue
FROM raw.orders o
LEFT JOIN revenue_cte r 
    ON o.order_id = r.order_id
WHERE EXTRACT(YEAR FROM o.order_purchase_timestamp) = 2017