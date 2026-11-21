/*
Q24: How many customers live in São Paulo state?
Customers as defined in docs/metrics.md: distinct customer_unique_id with at least one delivered order.
Location is customer_state on the order's customer record. A person with delivered orders in several
states counts if any of their delivered orders is in SP.
*/

SELECT
    COUNT(DISTINCT c.customer_unique_id) AS sp_customers_cnt
FROM raw.customers c
JOIN raw.orders o
    ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
  AND c.customer_state = 'SP';
