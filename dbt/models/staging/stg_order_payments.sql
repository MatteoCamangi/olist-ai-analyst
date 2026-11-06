
select
    {{ dbt_utils.generate_surrogate_key(['order_id', 'payment_sequential']) }} as order_payment_key,
    order_id,
    payment_sequential,
    payment_type,
    payment_installments,
    payment_value
from {{ source('raw', 'order_payments') }}