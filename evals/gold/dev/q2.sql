/*
Q2: Who are the top 10 customers by delivered revenue? (customer_unique_id)
*/

SELECT
    c.customer_unique_id,
    SUM(oi.price) AS customer_revenue
FROM raw.customers c 
INNER JOIN raw.orders o  
    ON c.customer_id = o.customer_id
INNER JOIN raw.order_items oi 
    ON o.order_id = oi.order_id
WHERE o.order_status = 'delivered'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10