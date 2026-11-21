/*
Q17: How many orders were returned?
Cannot be answered: the data has no returns. No order_status means returned, and no raw column
holds a return or a refund. Canceled and unavailable orders are not returns.
This query is the proof, not an answer.
Expected: returned_status_orders = 0 and return_or_refund_columns = 0.
*/

WITH status_cte AS (
    SELECT
        COUNT(*) FILTER (WHERE order_status ILIKE '%return%'
                            OR order_status ILIKE '%refund%'
                            OR order_status ILIKE '%devol%') AS returned_status_orders,
        COUNT(*) FILTER (WHERE order_status = 'canceled')    AS canceled_orders,
        COUNT(*) FILTER (WHERE order_status = 'unavailable') AS unavailable_orders,
        COUNT(DISTINCT order_status)                         AS distinct_statuses
    FROM raw.orders
),

columns_cte AS (
    SELECT
        COUNT(*) AS return_or_refund_columns
    FROM information_schema.columns
    WHERE table_schema = 'raw'
      AND (   column_name ILIKE '%return%'
           OR column_name ILIKE '%refund%'
           OR column_name ILIKE '%devol%')
)

SELECT
    s.returned_status_orders,
    c.return_or_refund_columns,
    s.canceled_orders,
    s.unavailable_orders,
    s.distinct_statuses
FROM status_cte s
CROSS JOIN columns_cte c;
