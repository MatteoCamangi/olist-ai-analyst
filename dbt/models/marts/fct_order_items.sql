SELECT
    i.order_item_key,
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    o.customer_unique_id,
    o.order_purchase_day,
    o.order_purchase_week,
    o.order_purchase_month,
    o.order_status,
    o.order_status_group,
    o.is_delivered,
    o.customer_city,
    o.customer_state,
    p.product_category_name_en,
    s.seller_city,
    s.seller_state,
    i.price AS item_price,
    i.freight_value AS item_freight_value
FROM {{ ref('stg_order_items') }} i
LEFT JOIN {{ ref('int_orders_enriched') }} o ON o.order_id = i.order_id
LEFT JOIN {{ ref('dim_products') }} p ON p.product_id = i.product_id
LEFT JOIN {{ ref('stg_sellers') }} s ON s.seller_id = i.seller_id
