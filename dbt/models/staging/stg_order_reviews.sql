select
    {{ dbt_utils.generate_surrogate_key(['review_id', 'order_id']) }} as review_order_key,
    review_id,
    order_id,
    review_score,
    review_comment_title,
    review_comment_message,
    review_creation_date,
    review_answer_timestamp
from {{ source('raw', 'order_reviews') }}