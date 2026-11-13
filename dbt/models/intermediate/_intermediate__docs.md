{% docs order_id %}
Identifier of the order (an anonymized hash). It links orders to their items, payments and reviews.
{% enddocs %}

{% docs seller_id %}
Identifier of the seller (an anonymized hash). The data has no seller names.
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

{% docs order_purchase_timestamp %}
Timestamp when the order was placed. Every purchase day, week and month in the project is derived from it.
{% enddocs %}

{% docs order_purchase_day %}
Calendar day of the purchase timestamp, as a date.
{% enddocs %}

{% docs order_purchase_week %}
Monday of the week of the purchase timestamp, as a date. Weeks run Monday to Sunday. Which weeks are complete is a reporting rule, see metrics.md, Part 2, Time and trends.
{% enddocs %}

{% docs order_purchase_month %}
First day of the month of the purchase timestamp, as a date. Which months are reliable is a reporting rule, see metrics.md.
{% enddocs %}

{% docs order_delivered_customer_date %}
Timestamp when the order was delivered to the customer. Null for orders not delivered. A few delivered orders have none and a few canceled orders have one, so use delivery_days or is_late, which apply the eligibility rule.
{% enddocs %}

{% docs order_estimated_delivery_date %}
Delivery date promised to the customer at purchase. Stored at midnight, so compare it by date, not by timestamp.
{% enddocs %}

{% docs delivery_days %}
Calendar days from purchase to delivery. Null unless the order is delivered and has a delivery date, so avg(delivery_days) is already over the eligible orders.
{% enddocs %}

{% docs is_late %}
True when delivery fell on a later calendar day than the estimated delivery date (compared as dates, not timestamps). False when delivered on time or earlier. Null when the order is not eligible, so count(is_late) is the eligible denominator.
{% enddocs %}

{% docs customer_state %}
Brazilian state (27 values) of the order's customer record. Location belongs to the order, not to the person.
{% enddocs %}

{% docs customer_city %}
City of the order's customer record, lowercase and without accents. The same name exists in several states, so always use it together with customer_state.
{% enddocs %}