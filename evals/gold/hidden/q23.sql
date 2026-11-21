/*
Q23: List of products generating at least 20K
Product ids with delivered revenue (sum of item price on delivered orders) of at least 20,000.
*/

SELECT
    oi.product_id,
    ROUND(SUM(oi.price), 2) AS delivered_revenue
FROM raw.order_items oi
JOIN raw.orders o
    ON oi.order_id = o.order_id
WHERE o.order_status = 'delivered'
GROUP BY oi.product_id
HAVING SUM(oi.price) >= 20000
ORDER BY oi.product_id;
