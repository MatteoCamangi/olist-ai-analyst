/*
Q21: What was the average delivery time for orders sent by Correios?
Cannot be answered: no raw column names the carrier. The only carrier-related column is
orders.order_delivered_carrier_date, a date (when the order was handed to a carrier), not a carrier.
This query is the proof. Expected: carrier_identity_columns = 0.
*/

SELECT
    COUNT(*) FILTER (WHERE data_type NOT IN ('DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE'))
        AS carrier_identity_columns,
    COUNT(*) FILTER (WHERE data_type IN ('DATE', 'TIMESTAMP', 'TIMESTAMP WITH TIME ZONE'))
        AS carrier_date_columns
FROM information_schema.columns
WHERE table_schema = 'raw'
  AND (   column_name ILIKE '%carrier%'
       OR column_name ILIKE '%courier%'
       OR column_name ILIKE '%correio%'
       OR column_name ILIKE '%logistic%'
       OR column_name ILIKE '%shipper%');
