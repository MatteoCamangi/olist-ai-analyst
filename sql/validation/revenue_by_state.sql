-- Delivered orders and revenue by customer state; the TOTAL row is the rollup
-- Expected: TOTAL 96478 orders and 13221498.11; SP 40501 and 5067633.16; RJ 12350 and 1759651.13; MG 11354 and 1552481.83
SELECT COALESCE(c.customer_state, 'TOTAL') AS customer_state,
       COUNT(DISTINCT o.order_id) AS delivered_orders,
       ROUND(SUM(i.price), 2)     AS revenue
FROM raw.orders o
JOIN raw.customers c USING (customer_id)
JOIN raw.order_items i USING (order_id)
WHERE o.order_status = 'delivered'
GROUP BY ROLLUP(c.customer_state)
ORDER BY revenue DESC;