/*
Q9: Which customer state has the highest average delivery time? (delivered orders only)
*/

SELECT
    c.customer_state,
    ROUND(AVG(o.order_delivered_customer_date::DATE - o.order_purchase_timestamp::DATE),1) AS delivery_days_avg
FROM raw.customers c 
INNER JOIN raw.orders o 
    ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered' 
AND order_purchase_timestamp IS NOT NULL
GROUP BY c.customer_state
ORDER BY 2 DESC 
LIMIT 1