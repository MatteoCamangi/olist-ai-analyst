{% docs order_id %}
Identifier of the order (an anonymized hash). It links orders to their items, payments and reviews.
{% enddocs %}

{% docs customer_id %}
Identifier of the customer record created for one order. It is unique per order, so it identifies an order's customer record, not a person. To count people, use `customer_unique_id`.
{% enddocs %}

{% docs customer_unique_id %}
Identifier of the person. The same person can have several orders, each with its own `customer_id`. Use it to count customers and repeat purchases. A person can appear with different states across orders (39 of 96,096 do), so never assign one state to a person: location belongs to the order.
{% enddocs %}

{% docs order_status %}
Status of the order at extraction time, with no history. One of: delivered, shipped, invoiced, processing, approved, created, canceled, unavailable. The value is spelled exactly like that, including `canceled` with one l.
{% enddocs %}

{% docs order_status_group %}
The order status collapsed into four groups: `delivered`; `in_flight` (shipped, invoiced, processing, approved, created); `canceled`; and `unavailable`. A status outside these maps to `other`, and a test fails if that happens. See metrics.md, Part 2, Status groups.
{% enddocs %}

{% docs is_delivered %}
True when the order status is `delivered` (96,478 orders). Revenue, delivery and review metrics use this population. Shipped orders are not delivered, even though some may have arrived.
{% enddocs %}