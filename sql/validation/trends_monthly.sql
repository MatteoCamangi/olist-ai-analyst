-- Delivered orders and revenue by purchase month, with month-over-month change. Window Jan 2017 to Jul 2018
-- Expected: 19 rows. Jan 2017 750 and 111798.36; Feb 2017 1653 and 234223.40 (+109.5% revenue);
-- Nov 2017 7289 and 987765.37 (+52.4% revenue, +62.8% orders); Jul 2018 6159 and 867953.46
WITH m AS (
    SELECT date_trunc('month', o.order_purchase_timestamp) AS month,
           COUNT(DISTINCT o.order_id) AS delivered_orders,
           ROUND(SUM(i.price), 2)     AS revenue
    FROM raw.orders o
    JOIN raw.order_items i USING (order_id)
    WHERE o.order_status = 'delivered'
      AND o.order_purchase_timestamp >= DATE '2017-01-01'
      AND o.order_purchase_timestamp <  DATE '2018-08-01'
    GROUP BY 1
)
SELECT month, delivered_orders, revenue,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY month))
             / LAG(revenue) OVER (ORDER BY month), 1) AS revenue_mom_pct,
       ROUND(100.0 * (delivered_orders - LAG(delivered_orders) OVER (ORDER BY month))
             / LAG(delivered_orders) OVER (ORDER BY month), 1) AS orders_mom_pct
FROM m
ORDER BY month;