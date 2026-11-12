WITH order_items AS (
    SELECT
        order_id,
        SUM(items_count) AS items_count,
        COUNT(*) AS sellers_count,
        SUM(items_price) AS order_price,
        SUM(items_freight_value) AS order_freight_value
    FROM {{ ref('int_order_sellers') }}
    GROUP BY order_id
)

SELECT
    o.order_id,
    o.customer_id,
    o.customer_unique_id,
    o.order_purchase_timestamp,
    o.order_purchase_day,
    o.order_purchase_week,
    o.order_purchase_month,
    o.order_status,
    o.order_status_group,
    o.is_delivered,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    o.delivery_days,
    o.is_late,
    o.customer_city,
    o.customer_state,
    COALESCE(i.items_count, 0) AS items_count,
    COALESCE(i.sellers_count, 0) AS sellers_count,
    i.order_price,
    i.order_freight_value,
    r.review_id,
    r.review_score
FROM {{ ref('int_orders_enriched') }} o
LEFT JOIN order_items i ON i.order_id = o.order_id
LEFT JOIN {{ ref('int_orders_latest_reviews') }} r ON r.order_id = o.order_id
