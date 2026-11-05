-- Top 10 cities (city + state) by delivered revenue
-- Expected: sao paulo SP 15045 orders and 1859556.83; rio de janeiro RJ 6601 and 955573.97; belo horizonte MG 2697 and 346039.04;
-- brasilia DF 2071 and 295814.72; curitiba PR 1489 and 205999.33. Top 10 total 4458132.81
SELECT c.customer_city, c.customer_state,
       COUNT(DISTINCT o.order_id) AS delivered_orders,
       ROUND(SUM(i.price), 2)     AS revenue
FROM raw.orders o
JOIN raw.customers c USING (customer_id)
JOIN raw.order_items i USING (order_id)
WHERE o.order_status = 'delivered'
GROUP BY 1, 2
ORDER BY 4 DESC
LIMIT 10;