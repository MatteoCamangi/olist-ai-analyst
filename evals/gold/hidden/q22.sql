/*
Q22: List every seller with at least 20 reviews and an average score of 4.8 or higher.
Delivered orders containing at least one of the seller's items. Each such order counts once per
seller, with its latest review (review_answer_timestamp, ties broken by review_id).
At least 20 reviewed orders; the average is not rounded before the 4.8 comparison.
*/

WITH latest_review AS (
    SELECT
        order_id,
        review_score
    FROM raw.order_reviews
    QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id
                               ORDER BY review_answer_timestamp DESC, review_id) = 1
),

seller_orders AS (
    SELECT DISTINCT
        oi.seller_id,
        oi.order_id
    FROM raw.order_items oi
    JOIN raw.orders o
        ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
)

SELECT
    so.seller_id,
    COUNT(r.review_score) AS reviewed_orders,
    AVG(r.review_score)   AS avg_score
FROM seller_orders so
LEFT JOIN latest_review r
    ON so.order_id = r.order_id
GROUP BY so.seller_id
HAVING COUNT(r.review_score) >= 20
   AND AVG(r.review_score) >= 4.8
ORDER BY so.seller_id;
