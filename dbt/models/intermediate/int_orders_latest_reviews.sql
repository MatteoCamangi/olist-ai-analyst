SELECT
    order_id,
    review_id,
    review_score
FROM {{ ref('stg_order_reviews') }}
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY order_id
    ORDER BY review_answer_timestamp DESC, review_id
) = 1