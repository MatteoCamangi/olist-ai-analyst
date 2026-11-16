/*
Q12: How many orders were placed in the last 90 days?
*/

WITH params_cte AS (
    SELECT 
        '2018-08-21'::DATE AS as_of_date
)

SELECT 
    COUNT(*) AS orders_last_90d
FROM raw.orders o 
CROSS JOIN params_cte p
WHERE o.order_purchase_timestamp::DATE >  p.as_of_date - 90
AND o.order_purchase_timestamp::DATE <= p.as_of_date