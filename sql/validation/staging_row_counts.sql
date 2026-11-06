-- Staging views must keep every raw row
-- Expected: raw_rows equals stg_rows on every line: 99441, 99441, 112650, 103886, 99224, 32951, 3095, 71
SELECT 'orders' AS table_name, (SELECT COUNT(*) FROM raw.orders) AS raw_rows, (SELECT COUNT(*) FROM staging.stg_orders) AS stg_rows
UNION ALL SELECT 'customers',   (SELECT COUNT(*) FROM raw.customers),       (SELECT COUNT(*) FROM staging.stg_customers)
UNION ALL SELECT 'order_items', (SELECT COUNT(*) FROM raw.order_items),     (SELECT COUNT(*) FROM staging.stg_order_items)
UNION ALL SELECT 'payments',    (SELECT COUNT(*) FROM raw.order_payments),  (SELECT COUNT(*) FROM staging.stg_order_payments)
UNION ALL SELECT 'reviews',     (SELECT COUNT(*) FROM raw.order_reviews),   (SELECT COUNT(*) FROM staging.stg_order_reviews)
UNION ALL SELECT 'products',    (SELECT COUNT(*) FROM raw.products),        (SELECT COUNT(*) FROM staging.stg_products)
UNION ALL SELECT 'sellers',     (SELECT COUNT(*) FROM raw.sellers),         (SELECT COUNT(*) FROM staging.stg_sellers)
UNION ALL SELECT 'translation', (SELECT COUNT(*) FROM raw.product_category_name_translation), (SELECT COUNT(*) FROM staging.stg_product_category_name_translation);
