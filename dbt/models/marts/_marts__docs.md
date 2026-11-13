{% docs first_order_day %}
Day of the customer's first delivered order, as a date. Only delivered orders count, so a canceled first order is ignored. "First" means first in the data, which starts in September 2016.
{% enddocs %}

{% docs first_order_review_score %}
Review score (1 to 5) of the customer's first delivered order. If that order has several reviews, the latest one counts. Null when the first order has no review. Never treat null as 0.
{% enddocs %}

{% docs is_repeat_customer %}
True when the customer has a delivered order on a later calendar day than their first delivered order. Several orders on the same day do not count (probably one basket split in two). There is no eligibility cutoff, so it is a floor on lifetime repeat behavior. See metrics.md, Customers and repeat rate.
{% enddocs %}

{% docs is_repeat_customer_180d %}
True when the customer has a delivered order 1 to 180 calendar days after their first delivered order. Null when the first order is too recent to have had the full 180 days (after 2018-02-22, the as-of date minus 180 days), so `count(is_repeat_customer_180d)` is the eligible denominator. See metrics.md.
{% enddocs %}

{% docs product_id %}
Identifier of the product (an anonymized hash). The data has no product names.
{% enddocs %}

{% docs product_category_name_pt %}
Category name in Portuguese, exactly as in the source. `unknown` when the product has no category.
{% enddocs %}

{% docs product_category_name_en %}
English category name. Rule, in this order: the official translation; a manual English name for the two Portuguese categories missing from the translation table (`portateis_cozinha_e_preparadores_de_alimentos` becomes `portable_kitchen_and_food_preparers`, `pc_gamer` stays `pc_gamer`); otherwise `unknown`. Names keep the typos of the source (for example `home_confort`), so never fix or merge them.
{% enddocs %}

{% docs order_price %}
Sum of item prices in the order, excluding freight. Revenue is sum(order_price) over delivered orders. Null when the order has no items. Gross amount: no refunds, costs or commission.
{% enddocs %}

{% docs order_review_score %}
Score (1 to 5) of the order's latest review (by review_answer_timestamp, ties broken by review_id). Null when the order has no review, never 0. Filled for every status, so filter on is_delivered for the review metric.
{% enddocs %}
