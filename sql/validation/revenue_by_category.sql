-- Revenue by English category, delivered orders. Missing category = 'unknown'. Two untranslated names mapped manually.
-- Expected: TOTAL row 110197 item_rows and 13221498.11; unknown 170726.63 (1.29%);
-- top: health_beauty 1233131.72, watches_gifts 1166176.98, bed_bath_table 1023434.76
WITH cat AS (
    SELECT COALESCE(
               t.product_category_name_english,
               CASE p.product_category_name
                   WHEN 'portateis_cozinha_e_preparadores_de_alimentos' THEN 'portable_kitchen_and_food_preparers'
                   WHEN 'pc_gamer' THEN 'pc_gamer'
               END,
               'unknown') AS category,
           COUNT(*) AS item_rows,
           ROUND(SUM(i.price), 2) AS revenue
    FROM raw.order_items i
    JOIN raw.orders o USING (order_id)
    JOIN raw.products p USING (product_id)
    LEFT JOIN raw.product_category_name_translation t
           ON p.product_category_name = t.product_category_name
    WHERE o.order_status = 'delivered'
    GROUP BY 1
)
SELECT category, item_rows, revenue,
       ROUND(100.0 * revenue / SUM(revenue) OVER (), 2) AS share_pct
FROM cat
UNION ALL
SELECT 'TOTAL', SUM(item_rows), ROUND(SUM(revenue), 2), 100.00 FROM cat
ORDER BY revenue DESC;