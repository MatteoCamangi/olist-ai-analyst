SELECT
    os.order_seller_key,
    os.order_id,
    os.seller_id,
    oe.order_purchase_timestamp,
    oe.order_purchase_day,
    oe.order_purchase_week,
    oe.order_purchase_month,
    oe.order_status,
    oe.order_status_group,
    oe.is_delivered,
    oe.delivery_days,
    oe.is_late,
    olr.review_score,
    oe.customer_city,
    oe.customer_state,
    s.seller_city,
    s.seller_state,
    os.items_count,
    os.items_price,
    os.items_freight_value
FROM {{ ref('int_order_sellers') }} os
LEFT JOIN {{ ref('int_orders_enriched') }}       oe  ON oe.order_id = os.order_id
LEFT JOIN {{ ref('int_orders_latest_reviews') }} olr ON olr.order_id = os.order_id
LEFT JOIN {{ ref('stg_sellers') }}               s   ON s.seller_id = os.seller_id
