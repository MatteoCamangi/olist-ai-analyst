-- Top 10 sellers by delivered revenue, plus a totals row for the consistency check
-- Expected: top seller 4869f7a5dfa277a7dca6462dcf3b52b2 (SP) 1124 orders and 226987.93; top 10 total 1754800.00;
-- SUM OF ALL SELLERS row: 97819 orders (more than the 96478 delivered orders) and revenue 13221498.11
WITH per_seller AS (
    SELECT i.seller_id, s.seller_state,
           COUNT(DISTINCT i.order_id) AS delivered_orders,
           ROUND(SUM(i.price), 2)     AS revenue
    FROM raw.order_items i
    JOIN raw.orders o USING (order_id)
    JOIN raw.sellers s USING (seller_id)
    WHERE o.order_status = 'delivered'
    GROUP BY 1, 2
),
top10 AS (
    SELECT 1 AS sort_key, seller_id, seller_state, delivered_orders, revenue
    FROM per_seller
    ORDER BY revenue DESC
    LIMIT 10
),
totals AS (
    SELECT 2 AS sort_key, 'SUM OF ALL SELLERS' AS seller_id, CAST(NULL AS VARCHAR) AS seller_state,
           SUM(delivered_orders) AS delivered_orders, ROUND(SUM(revenue), 2) AS revenue
    FROM per_seller
)
SELECT * FROM top10
UNION ALL
SELECT * FROM totals
ORDER BY sort_key, revenue DESC;