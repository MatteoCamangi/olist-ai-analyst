-- fct_order_items must keep every item line of stg_order_items (no rows lost or fanned out by joins).
select f.n as fct_rows, s.n as stg_rows
from (select count(*) as n from {{ ref('fct_order_items') }}) f
cross join (select count(*) as n from {{ ref('stg_order_items') }}) s
where f.n <> s.n
