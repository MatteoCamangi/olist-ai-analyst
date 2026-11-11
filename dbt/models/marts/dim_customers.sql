WITH delivered_orders AS (
    SELECT
        customer_unique_id,
        order_id,
        order_purchase_day,
        MIN(order_purchase_day) OVER (PARTITION BY customer_unique_id) AS first_order_day
    FROM {{ ref('int_orders_enriched') }}
    WHERE is_delivered
),

first_orders AS (
    SELECT
        customer_unique_id,
        order_id AS first_order_id
    FROM {{ ref('int_orders_enriched') }}
    WHERE is_delivered
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY customer_unique_id
        ORDER BY order_purchase_timestamp, order_id
    ) = 1
),

per_customer AS (
    SELECT
        customer_unique_id,
        first_order_day,
        COUNT(*) AS delivered_orders_count,
        BOOL_OR(order_purchase_day > first_order_day) AS is_repeat_customer,
        BOOL_OR(
            order_purchase_day > first_order_day
            AND order_purchase_day <= first_order_day + 180
        ) AS returned_within_180d
    FROM delivered_orders
    GROUP BY customer_unique_id, first_order_day
)

SELECT
    pc.customer_unique_id,
    pc.first_order_day,
    pc.delivered_orders_count,
    lr.review_score AS first_order_review_score,
    pc.is_repeat_customer,
    CASE
        WHEN pc.first_order_day <= DATE '{{ var("as_of_date") }}' - 180
        THEN pc.returned_within_180d
    END AS is_repeat_customer_180d
FROM per_customer pc
LEFT JOIN first_orders fo ON fo.customer_unique_id = pc.customer_unique_id
LEFT JOIN {{ ref('int_orders_latest_reviews') }} lr ON lr.order_id = fo.first_order_id