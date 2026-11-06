select
    {{ dbt_utils.generate_surrogate_key(['order_id', 'order_item_id']) }} as order_item_key,
    order_id,
    order_item_id,
    product_id,
    seller_id,
    shipping_limit_date,
    price,
    freight_value
from {{ source('raw', 'order_items') }}