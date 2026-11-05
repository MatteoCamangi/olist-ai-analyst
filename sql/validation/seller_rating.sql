-- Top 10 sellers by average rating, at least 5 reviewed delivered orders.
-- Ties: reviewed orders (more first), then revenue
-- Expected: qualified_sellers 1761; the top 10 all average 5.0 with reviewed orders 33, 12, 9, 8, 8, 8, 8, 7, 7, 7
WITH latest AS (
    SELECT order_id, review_score
    FROM raw.order_reviews
    QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id ORDER BY review_answer_timestamp DESC, review_id) = 1
),
seller_orders AS (
    SELECT DISTINCT i.seller_id, i.order_id
    FROM raw.order_items i
    JOIN raw.orders o USING (order_id)
    WHERE o.order_status = 'delivered'
),
seller_revenue AS (
    SELECT i.seller_id, SUM(i.price) AS revenue
    FROM raw.order_items i
    JOIN raw.orders o USING (order_id)
    WHERE o.order_status = 'delivered'
    GROUP BY 1
)
SELECT so.seller_id, s.seller_state,
       COUNT(*)                      AS reviewed_orders,
       ROUND(AVG(l.review_score), 2) AS avg_rating,
       ROUND(r.revenue, 2)           AS revenue,
       COUNT(*) OVER ()              AS qualified_sellers
FROM seller_orders so
JOIN latest l USING (order_id)
JOIN seller_revenue r USING (seller_id)
JOIN raw.sellers s USING (seller_id)
GROUP BY so.seller_id, s.seller_state, r.revenue
HAVING COUNT(*) >= 5
ORDER BY AVG(l.review_score) DESC, COUNT(*) DESC, r.revenue DESC
LIMIT 10;