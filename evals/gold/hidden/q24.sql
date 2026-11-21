/*
Q24: How many customers live in São Paulo state?
Customers = distinct customer_unique_id with an order of any status and customer_state = 'SP'.
A person with order records in several states counts if any record is in SP.
*/

SELECT
    COUNT(DISTINCT c.customer_unique_id) AS sp_customers_cnt
FROM raw.customers c
JOIN raw.orders o
    ON c.customer_id = o.customer_id
WHERE c.customer_state = 'SP';
