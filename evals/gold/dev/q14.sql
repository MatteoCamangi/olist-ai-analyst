/*
Q14: Why did revenue drop in 2019 compared to 2018?
False premise: the data has no 2019 orders. This query is the proof, not an answer.
Expected: orders_2019 = 0, and the last order is in 2018.
*/

SELECT
    COUNT(*) FILTER (WHERE EXTRACT(year FROM order_purchase_timestamp) = 2019) AS orders_2019,
    MIN(order_purchase_timestamp)::DATE AS first_order_date,
    MAX(order_purchase_timestamp)::DATE AS last_order_date
FROM raw.orders;