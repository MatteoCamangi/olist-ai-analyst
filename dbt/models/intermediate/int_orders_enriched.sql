SELECT
    o.order_id,
    c.customer_id,
    c.customer_unique_id,
    o.order_purchase_timestamp,
    CAST(DATE_TRUNC('day', o.order_purchase_timestamp) AS DATE) AS order_purchase_day,
    CAST(DATE_TRUNC('week', o.order_purchase_timestamp) AS DATE) AS order_purchase_week,
    CAST(DATE_TRUNC('month', o.order_purchase_timestamp) AS DATE) AS order_purchase_month,
    o.order_status,
    CASE
        WHEN o.order_status = 'delivered' THEN 'delivered'
        WHEN o.order_status IN ('shipped', 'invoiced', 'processing', 'approved', 'created') THEN 'in_flight'
        WHEN o.order_status = 'canceled' THEN 'canceled'
        WHEN o.order_status = 'unavailable' THEN 'unavailable'
        ELSE 'other'
    END AS order_status_group,
    o.order_status = 'delivered' AS is_delivered,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,
    CASE
        WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
            THEN DATE_DIFF('day', o.order_purchase_timestamp, o.order_delivered_customer_date)
    END AS delivery_days,
    CASE
        WHEN o.order_status = 'delivered' AND o.order_delivered_customer_date IS NOT NULL
            THEN CAST(o.order_delivered_customer_date AS DATE) > CAST(o.order_estimated_delivery_date AS DATE)
    END AS is_late,
    c.customer_city,
    c.customer_state
FROM {{ ref('stg_orders') }} o 
LEFT JOIN {{ ref('stg_customers') }} c 
    ON o.customer_id = c.customer_id