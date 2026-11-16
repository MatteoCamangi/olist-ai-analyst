/*
Q16: Who are our best customers?
Ambiguous on purpose. Accepted interpretations (customer = customer_unique_id,
delivered orders only):
  1. by_revenue : top 10 by delivered revenue
  2. by_orders  : top 10 by number of delivered orders (tie-break: revenue)
An answer must state which interpretation it uses and match that ranking.
*/

WITH delivered_orders AS (
    SELECT 
        o.order_id, 
        c.customer_unique_id
    FROM raw.orders o
    JOIN raw.customers c 
        ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
),

order_revenue AS (
    SELECT 
        order_id, 
        SUM(price) AS revenue
    FROM raw.order_items
    GROUP BY order_id
),

customer_stats AS (
    SELECT
        d.customer_unique_id,
        COUNT(*)       AS delivered_orders,
        SUM(r.revenue) AS delivered_revenue
    FROM delivered_orders d
    LEFT JOIN order_revenue r ON d.order_id = r.order_id
    GROUP BY d.customer_unique_id
)

(SELECT
    'by_revenue' AS interpretation,
    ROW_NUMBER() OVER (ORDER BY delivered_revenue DESC) AS rnk,
    customer_unique_id, 
    delivered_orders, 
    ROUND(delivered_revenue, 2) AS delivered_revenue
 FROM customer_stats
 ORDER BY rnk
 LIMIT 10)

UNION ALL

(SELECT
    'by_orders' AS interpretation,
    ROW_NUMBER() OVER (ORDER BY delivered_orders DESC, delivered_revenue DESC) AS rnk,
    customer_unique_id, 
    delivered_orders, 
    ROUND(delivered_revenue, 2) AS delivered_revenue
 FROM customer_stats
 ORDER BY rnk
 LIMIT 10);