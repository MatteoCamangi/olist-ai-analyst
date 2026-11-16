/*
Q3: Who are the top 10 sellers by delivered revenue?
*/

SELECT
    s.seller_id,
    SUM(oi.price) AS seller_revenue
FROM raw.sellers s
INNER JOIN raw.order_items oi 
    ON s.seller_id = oi.seller_id
INNER JOIN raw.orders o  
    ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY 1
ORDER BY 2 DESC
LIMIT 10