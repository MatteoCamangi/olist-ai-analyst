SELECT
    p.product_id,
    COALESCE(p.product_category_name, 'unknown') AS product_category_name_pt,
    COALESCE(
        pt.product_category_name_english,
        CASE p.product_category_name
            WHEN 'portateis_cozinha_e_preparadores_de_alimentos' THEN 'portable_kitchen_and_food_preparers'
            WHEN 'pc_gamer' THEN 'pc_gamer'
        END,
        'unknown'
    ) AS product_category_name_en
FROM {{ ref('stg_products') }} p 
LEFT JOIN {{ ref('stg_product_category_name_translation') }} pt 
    ON p.product_category_name = pt.product_category_name