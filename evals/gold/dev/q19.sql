/*
Q19: What is the average review score of delivered orders?
Latest review per order (review_answer_timestamp, ties broken by review_id), delivered orders only,
orders without a review excluded, average per order.
*/

WITH latest_review AS (
    SELECT
        order_id,
        review_score
    FROM raw.order_reviews
    QUALIFY ROW_NUMBER() OVER (PARTITION BY order_id
                               ORDER BY review_answer_timestamp DESC, review_id) = 1
)

SELECT
    ROUND(AVG(r.review_score), 3) AS avg_review_score
FROM raw.orders o
JOIN latest_review r
    ON o.order_id = r.order_id
WHERE o.order_status = 'delivered';
