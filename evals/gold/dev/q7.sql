/*
Q7: What are the top 5 best-selling products by delivered revenue? (product_id)
*/

SELECT oi.product_id, SUM(oi.price) AS revenue
FROM raw.order_items oi
JOIN raw.orders o 
    ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY oi.product_id
ORDER BY revenue DESC, oi.product_id
LIMIT 5;