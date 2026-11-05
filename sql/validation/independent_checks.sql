-- Independent recomputation from the raw CSV files, bypassing the raw schema and the structure of the other files
-- Expected: delivered_revenue 13221498.11 | delivered_orders 96478 | late_rate_pct 6.77 | repeat_customers 2015
-- Allow a difference of 0.01 on revenue (price is a floating-point column)
WITH o AS (SELECT * FROM read_csv_auto('data/raw/olist_orders_dataset.csv')),
     i AS (SELECT * FROM read_csv_auto('data/raw/olist_order_items_dataset.csv')),
     c AS (SELECT * FROM read_csv_auto('data/raw/olist_customers_dataset.csv'))
SELECT 'delivered_revenue' AS check_name, ROUND(SUM(i.price), 2) AS value
FROM i JOIN o ON i.order_id = o.order_id
WHERE o.order_status = 'delivered'
UNION ALL
SELECT 'delivered_orders', COUNT(*) FROM o WHERE order_status = 'delivered'
UNION ALL
SELECT 'late_rate_pct',
       ROUND(100.0 * COUNT(*) FILTER (WHERE date_diff('day', CAST(order_estimated_delivery_date AS DATE),
                                                       CAST(order_delivered_customer_date AS DATE)) >= 1) / COUNT(*), 2)
FROM o WHERE order_status = 'delivered' AND order_delivered_customer_date IS NOT NULL
UNION ALL
SELECT 'repeat_customers', COUNT(*)
FROM (
    SELECT c.customer_unique_id
    FROM o JOIN c ON o.customer_id = c.customer_id
    WHERE o.order_status = 'delivered'
    GROUP BY 1
    HAVING COUNT(DISTINCT CAST(o.order_purchase_timestamp AS DATE)) >= 2
);