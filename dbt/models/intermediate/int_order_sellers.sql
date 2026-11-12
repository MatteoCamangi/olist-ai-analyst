SELECT
    {{ dbt_utils.generate_surrogate_key(['order_id', 'seller_id']) }} AS order_seller_key,
    order_id,
    seller_id,
    COUNT(*) AS items_count,
    SUM(price) AS items_price,
    SUM(freight_value) AS items_freight_value
FROM {{ ref('stg_order_items') }}
GROUP BY order_id, seller_id
