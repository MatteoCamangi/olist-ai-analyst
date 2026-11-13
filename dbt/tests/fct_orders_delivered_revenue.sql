-- Delivered revenue must tie to the validated value in docs/metrics.md (13,221,498.11).
select delivered_revenue
from (
    select sum(order_price) filter (where is_delivered) as delivered_revenue
    from {{ ref('fct_orders') }}
)
where delivered_revenue is null
   or abs(delivered_revenue - 13221498.11) >= 0.005
