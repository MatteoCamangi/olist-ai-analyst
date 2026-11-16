/*
Q5: Where do most orders come from? Show the top 5 customer cities (delivered orders only).
*/

SELECT 
    c.customer_city,
    c.customer_state,
    COUNT(*) AS orders_cnt
FROM raw.customers c 
INNER JOIN raw.orders o 
    ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
GROUP BY 1,2
ORDER BY 3 DESC
LIMIT 5