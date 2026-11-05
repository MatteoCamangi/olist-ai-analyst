-- Products ranked by volume (item rows) and by revenue, delivered orders. Shows every product in the top 10 of either ranking
-- Expected: by volume rank 1 aca2eb7d00ea1a7b8ebd4e68314663af (furniture_decor) 520 rows and 37104.30; rank 2 422879e10f46682990de24d770e7f83d (garden_tools) 484 rows;
-- d1c427060a0f73f6b889a5c7c61f2ac4 (computers_accessories) has 332 rows (volume rank 7) and 45620.56 revenue
WITH per_product AS (
    SELECT i.product_id,
           COALESCE(
               t.product_category_name_english,
               CASE p.product_category_name
                   WHEN 'portateis_cozinha_e_preparadores_de_alimentos' THEN 'portable_kitchen_and_food_preparers'
                   WHEN 'pc_gamer' THEN 'pc_gamer'
               END,
               'unknown') AS category,
           COUNT(*)               AS item_rows,
           ROUND(SUM(i.price), 2) AS revenue
    FROM raw.order_items i
    JOIN raw.orders o USING (order_id)
    JOIN raw.products p USING (product_id)
    LEFT JOIN raw.product_category_name_translation t
           ON p.product_category_name = t.product_category_name
    WHERE o.order_status = 'delivered'
    GROUP BY 1, 2
),
ranked AS (
    SELECT *,
           RANK() OVER (ORDER BY item_rows DESC) AS rank_by_volume,
           RANK() OVER (ORDER BY revenue DESC)   AS rank_by_revenue
    FROM per_product
)
SELECT * FROM ranked
WHERE rank_by_volume <= 10 OR rank_by_revenue <= 10
ORDER BY rank_by_revenue;