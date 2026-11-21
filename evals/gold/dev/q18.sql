/*
Q18: Which product category performs best?
Ambiguous on purpose. Accepted interpretations (delivered orders only, English category name
with the manual mapping for two untranslated names, missing category = 'unknown'):
  1. by_revenue : top 1 category by delivered revenue (sum of item price)
  2. by_orders  : top 1 category by delivered orders (an order counts once per category)
An answer must state which interpretation it uses and match that ranking.
*/

WITH delivered_items AS (
    SELECT
        oi.order_id,
        oi.price,
        COALESCE(
            t.product_category_name_english,
            CASE p.product_category_name
                WHEN 'portateis_cozinha_e_preparadores_de_alimentos' THEN 'portable_kitchen_and_food_preparers'
                WHEN 'pc_gamer' THEN 'pc_gamer'
            END,
            'unknown'
        ) AS product_category
    FROM raw.order_items oi
    JOIN raw.orders o
        ON oi.order_id = o.order_id
    LEFT JOIN raw.products p
        ON oi.product_id = p.product_id
    LEFT JOIN raw.product_category_name_translation t
        ON p.product_category_name = t.product_category_name
    WHERE o.order_status = 'delivered'
),

category_stats AS (
    SELECT
        product_category,
        SUM(price)               AS delivered_revenue,
        COUNT(DISTINCT order_id) AS delivered_orders
    FROM delivered_items
    GROUP BY product_category
)

(SELECT
    'by_revenue' AS interpretation,
    ROW_NUMBER() OVER (ORDER BY delivered_revenue DESC) AS rnk,
    product_category,
    ROUND(delivered_revenue, 2) AS delivered_revenue,
    delivered_orders
 FROM category_stats
 ORDER BY rnk
 LIMIT 1)

UNION ALL

(SELECT
    'by_orders' AS interpretation,
    ROW_NUMBER() OVER (ORDER BY delivered_orders DESC, delivered_revenue DESC) AS rnk,
    product_category,
    ROUND(delivered_revenue, 2) AS delivered_revenue,
    delivered_orders
 FROM category_stats
 ORDER BY rnk
 LIMIT 1);
