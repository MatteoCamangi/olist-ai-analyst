-- Delivered revenue in fct_order_items must agree with fct_orders.
select i.revenue as items_revenue, o.revenue as orders_revenue
from (select sum(item_price) filter (where is_delivered) as revenue from {{ ref('fct_order_items') }}) i
cross join (select sum(order_price) filter (where is_delivered) as revenue from {{ ref('fct_orders') }}) o
where i.revenue is null
   or o.revenue is null
   or abs(i.revenue - o.revenue) >= 0.005
