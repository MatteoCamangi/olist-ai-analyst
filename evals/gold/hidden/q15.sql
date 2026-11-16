/*
Q15: What is the profit margin by product category?
Cannot be answered: the raw tables have no cost, margin or profit column.
This query is the proof. Expected: no rows with kind = 'cost_or_profit'.
The only money columns are revenue-side (price, freight_value, payment_value).
*/

SELECT
    table_name,
    column_name,
    CASE
        WHEN column_name ILIKE '%cost%'
          OR column_name ILIKE '%margin%'
          OR column_name ILIKE '%profit%'
        THEN 'cost_or_profit'
        ELSE 'revenue_side'
    END AS kind
FROM information_schema.columns
WHERE table_schema = 'raw'
  AND (   column_name ILIKE '%cost%'
       OR column_name ILIKE '%margin%'
       OR column_name ILIKE '%profit%'
       OR column_name ILIKE '%price%'
       OR column_name ILIKE '%freight%'
       OR column_name ILIKE '%value%')
ORDER BY kind, table_name, column_name;