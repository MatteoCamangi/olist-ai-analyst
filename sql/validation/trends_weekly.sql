-- Delivered orders and revenue by Monday to Sunday week, complete weeks only (2017-01-02 to 2018-08-19)
-- Expected: 85 rows. First week 2017-01-02: 44 orders and 3114.96. Last week 2018-08-13: 1839 orders and 243458.02 (-9.9% revenue)
WITH w AS (
    SELECT date_trunc('week', o.order_purchase_timestamp) AS week_start,
           COUNT(DISTINCT o.order_id) AS delivered_orders,
           ROUND(SUM(i.price), 2)     AS revenue
    FROM raw.orders o
    JOIN raw.order_items i USING (order_id)
    WHERE o.order_status = 'delivered'
      AND CAST(o.order_purchase_timestamp AS DATE) BETWEEN DATE '2017-01-02' AND DATE '2018-08-19'
    GROUP BY 1
)
SELECT week_start, delivered_orders, revenue,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY week_start))
             / LAG(revenue) OVER (ORDER BY week_start), 1) AS revenue_wow_pct,
       ROUND(100.0 * (delivered_orders - LAG(delivered_orders) OVER (ORDER BY week_start))
             / LAG(delivered_orders) OVER (ORDER BY week_start), 1) AS orders_wow_pct
FROM w
ORDER BY week_start;