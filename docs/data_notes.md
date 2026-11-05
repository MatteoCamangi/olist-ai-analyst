# Olist data notes

Purpose: facts and evidence about the data. Definitions and answering rules are in metrics.md. If the two files disagree, metrics.md wins. Every number says whether it covers all orders ("all statuses") or delivered orders only.

## 1. Tables and grain
- orders: one row per order (order_id unique, no nulls), 99,441 rows. Holds the current status only, with no status history
- customers: one row per customer_id (99,441 rows). customer_id is order-level, so count people with customer_unique_id
- order_items: one row per item line in an order (order_id + order_item_id unique, 0 duplicates), 112,650 rows. Orders can have several items
- order_payments: one row per payment of an order (order_id + payment_sequential, 0 duplicates), 103,886 rows. An order can have several payment rows
- order_reviews: one row per review, 99,224 rows. An order can have several reviews
- products: one row per product_id, 32,951 rows
- sellers: one row per seller_id, 3,095 rows
- product_category_name_translation: one row per category, 71 rows, one-to-one Portuguese to English
- geolocation: not analysed, not used

## 2. Columns (verified from information_schema, names exactly as in the source)
- orders: order_id, customer_id, order_status, order_purchase_timestamp, order_approved_at, order_delivered_carrier_date, order_delivered_customer_date, order_estimated_delivery_date (all dates are TIMESTAMP)
- customers: customer_id, customer_unique_id, customer_zip_code_prefix, customer_city, customer_state
- order_items: order_id, order_item_id, product_id, seller_id, shipping_limit_date, price, freight_value
- order_payments: order_id, payment_sequential, payment_type, payment_installments, payment_value
- order_reviews: review_id, order_id, review_score, review_comment_title, review_comment_message, review_creation_date, review_answer_timestamp
- products: product_id, product_category_name, product_name_lenght, product_description_lenght, product_photos_qty, product_weight_g, product_length_cm, product_height_cm, product_width_cm
- sellers: seller_id, seller_zip_code_prefix, seller_city, seller_state
- product_category_name_translation: product_category_name, product_category_name_english
- product_name_lenght and product_description_lenght are misspelled in the source. Never correct them in SQL
- Zip code prefixes are text (VARCHAR). price, freight_value and payment_value are DOUBLE

## 3. Data window and calendar
- Order dates run from 2016-09-04 to 2018-10-17 (order_purchase_timestamp)
- 2016 is sparse (all statuses): Sep 4 orders, Oct 324, Nov none, Dec 1
- Early 2017 ramp-up (delivered orders): Jan 750, Feb 1,653 (+120.4%), Mar 2,546 (+54.0%). All statuses: Jan 800, Feb 1,780
- Daily volume (all statuses) falls away from late August 2018: 243 orders on Aug 21, 187 on Aug 22, 144 on Aug 23, 99 on Aug 24, then 69, 73, 67, 44, 14, 4 and 1 on Aug 25-31. Sep 2018 (16 orders) and Oct 2018 (4) are scattered single-digit days. Cause unknown, probably how the dataset was cut and not a real drop in sales
- Last healthy day: 2018-08-21. August 2018 has 6,512 orders (all statuses) and is partial, understated by the thin tail. Last complete month: 2018-07
- Last 7 days to the as-of date (2018-08-15 to 2018-08-21): 1,766 orders placed, all statuses
- Weeks start on Monday (confirmed with date_trunc('week')). 2017-01-01 is a Sunday. There are 85 complete weeks from 2017-01-02 to 2018-08-19
- Black Friday: 1,176 orders (all statuses) on 2017-11-24 against 149 to 283 per day on Nov 15-23. The days after stay elevated (499, 391, 403, 380 on Nov 25-28). November 2017 has 7,544 orders (all statuses) and 7,289 delivered orders (+52.4% revenue and +62.8% orders against October)
- Weekly volatility: about half of the 84 weekly changes in delivered revenue exceed ±10%. Largest: week of 2017-11-20 2,915 orders and 397,906.24 (+131.4% revenue, Black Friday); week of 2018-05-21 941 orders and 132,510.93 (−48.1% revenue; probably the nationwide truckers' strike in Brazil, from my recollection and not in the data), recovering to 1,075 orders the week after; week of 2017-08-28 979 orders and 177,090.57 (+70.0% revenue against +13.6% orders, cause unknown); week of 2018-01-01 1,341 orders (+79.9% revenue after the low holiday weeks)

## 4. Facts by topic

### Orders and status
- order_status counts (all statuses, 99,441 orders): delivered 96,478 (97.0%); shipped 1,107; canceled 625; unavailable 609; invoiced 314; processing 301; created 5; approved 2
- Orders without items (all statuses): delivered 0; shipped 1; canceled 164; unavailable 603 of 609; invoiced 2; created 5 of 5. 775 orders have no items, so 98,666 orders have items
- Delivery dates: 96,470 of 96,478 delivered orders have order_delivered_customer_date (8 missing). 6 canceled orders also have one. No delivered order was delivered before purchase and none lacks an estimated date
- Shipped orders (1,107) are spread across every month, not only the last ones. None has a delivery date. Whether they were delivered is unknown. They are not counted as delivered orders or in revenue

### Items and revenue
- Item price by status: delivered 13,221,498.11 (97.3%); shipped 150,727.44; canceled 95,235.27; unavailable 2,007.69; invoiced 61,526.37; processing 60,439.22; approved 209.60; created none. Total 13,591,643.70
- Delivered items: 110,197 rows, all 96,478 delivered orders have items
- Freight totals 2,251,909.54 across all statuses (16.6% on top of item price)
- order_item_id is a per-order counter (1 for the first item, 2 for the second), with 21 distinct values and no gaps in any order. The largest order has 21 item lines
- There is no quantity column. Two units of one product appear as two item rows

### Payments
- Payment rows (103,886) outnumber orders, because an order can be paid in several parts. Joining them multiplies item rows (see Traps)
- Payment types (all statuses, 103,886 payment rows): credit_card 76,795 payments over 76,505 orders (12,542,084.19, average 3.51 installments, maximum 24); boleto 19,784 over 19,784 (2,869,361.27); voucher 5,775 over 3,866 (379,436.87, so an order can have several voucher payments); debit_card 1,529 over 1,528 (217,989.79); not_defined 3 payments with value 0.00
- payment_value includes freight. On delivered orders with a payment row, payments total 15,422,461.77 against price plus freight 15,419,630.29 (0.02% apart) and price alone 13,221,363.14
- The item price on delivered orders with a payment row is 134.97 below total delivered revenue (13,221,498.11), so at least one delivered order has items but no payment row

### Reviews
- 98,126 orders have 1 review, 543 have 2 and 4 have 3 (98,673 reviewed orders). 768 orders have no review
- 547 orders have several reviews. 202 of them have differing scores (about 0.2% of reviewed orders)
- Delivered orders: 95,832 have a review, 646 have none. Average latest-review score 4.156
- All statuses: 98,673 reviewed orders, average 4.086, so undelivered orders score much lower

### Customers and repeat behavior
- 93,358 people have a delivered order
- 2,801 of them have a second delivered order. 786 of those only placed same-day extra orders (probably one basket split into several orders). 2,015 (2.16%) placed an order at least 1 day after their first
- Time from first to second delivered order, for the 2,801: same day 829; 1 to 30 days 587; 31 to 90 days 492; 91 to 180 days 413; over 180 days 480
- All statuses: about 93,100 people ordered once and about 2,700 twice, with a long tail

### Geography
- customers has 99,441 rows, 27 states, 4,119 distinct city names and 4,310 distinct city + state pairs, no missing state or city. A customers row is an order-level record, so counts of rows by city or state are order counts, not people
- City names are lowercase without accents. Lowercasing, accent-stripping and trimming merges no names (4,119 before and after). Sao Paulo has one spelling, with 15,540 customer rows
- The same city name exists in several states: Santo Andre 797 customer rows across 2 states, Belem 447 across 3, Vitoria 380 across 2, Praia Grande 316 across 3, Sao Carlos 241 across 2, Santa Maria 197 across 4, and others. Sao Paulo exists in one state only
- Delivered orders and item revenue by customer state tie to 96,478 and 13,221,498.11. SP 40,501 orders and 5,067,633.16 (38.3% of revenue, 42.0% of delivered orders); RJ 12,350 and 1,759,651.13; MG 11,354 and 1,552,481.83. SP, RJ and MG together are 66.5% of delivered orders and 63.4% of revenue. Smallest: RR 41 orders and 7,057.47
- Top cities by delivered revenue (city + state): sao paulo SP 15,045 orders and 1,859,556.83 (14.1% of revenue, 37.1% of SP state orders); rio de janeiro RJ 6,601 and 955,573.97; belo horizonte MG 2,697 and 346,039.04; brasilia DF 2,071 and 295,814.72 (DF has 2,080); curitiba PR 1,489 and 205,999.33. The top 10 cities total 33.7% of revenue

### Sellers
- 2,970 of 3,095 sellers have at least one delivered order
- 1,278 of 98,666 orders with items (1.30%) contain items from more than one seller. Some orders have three or more
- Seller revenue sums exactly to 13,221,498.11. Seller order counts sum to 97,819 against 96,478 delivered orders (1,341 more, 1.39%), because multi-seller orders count once per seller
- Reviewed delivered orders per seller: at least 5 for 1,761 sellers; at least 10 for 1,236; at least 20 for 800; at least 30 for 623; at least 50 for 418
- Top 10 sellers by delivered revenue total 1,754,800.00 (13.3% of revenue). 9 of 10 are in SP. The top seller has 226,987.93 across 1,124 delivered orders
- The ten best-rated sellers with at least 5 reviewed orders all average 5.0 (reviewed orders: 33, 12, 9, 8, 8, 8, 8, 7, 7, 7), so ties exist

### Products and categories
- Joining items to products drops nothing: delivered items 110,197 rows and 13,221,498.11
- 1,537 item rows (170,726.63, 1.29% of revenue) belong to products with no category
- 22 item rows (5,240.58) have Portuguese categories missing from the translation table: portateis_cozinha_e_preparadores_de_alimentos (14 rows, 3,933.63) and pc_gamer (8 rows, 1,306.95)
- The category list has 74 values: 71 translated categories, the 2 untranslated ones and 'unknown'. Category revenue sums to 13,221,498.11
- Rankings by volume and by revenue differ: only 5 of the top 10 products by volume are also in the top 10 by revenue. The third product by revenue (d6160fb7873f184099d9bc95e30376af, computers) has volume rank 315
- Top 3 products by revenue: bb50f2e236e5eea0100680137654686c (health_beauty) 194 item rows and 63,560.00; 6cdd53843498f92890544667809f1595 (health_beauty) 153 and 53,652.30; d6160fb7873f184099d9bc95e30376af (computers) 33 and 45,949.35. Top 3 by volume: aca2eb7d00ea1a7b8ebd4e68314663af (furniture_decor) 520 rows; 422879e10f46682990de24d770e7f83d (garden_tools) 484; 99a4788cb24856965c36a24e339b6058 (bed_bath_table) 477. Four of the top six by volume are garden_tools
- Top 5 categories by revenue: health_beauty 1,233,131.72; watches_gifts 1,166,176.98; bed_bath_table 1,023,434.76; sports_leisure 954,852.55; computers_accessories 888,724.61. The smallest is security_and_services with 283.29

## 5. Traps (Problem, Evidence, Rule)
- customer_id is not a person. Problem: counting customer_id counts orders. Evidence: unique per order in both customers and orders. Rule: count people with customer_unique_id
- Payments join. Problem: joining order_items to order_payments repeats item rows once per extra payment row. Evidence: 112,650 item rows become 117,600 and revenue goes from 13,591,643.70 to 14,209,115.34 (4.5% too high); aggregating payments to one row per order first returns 112,650 rows and 13,591,643.70. Rule: reduce order_payments to one row per order before joining
- Reviews join. Problem: joining raw reviews duplicates orders with several reviews. Evidence: 547 orders have 2 or 3 reviews. Rule: take the latest review per order first (see metrics.md)
- Midnight estimates. Problem: estimated delivery dates are stored at midnight, so comparing timestamps marks same-day deliveries as late. Evidence: 7,826 delivered orders late by timestamp (8.11%) against 6,534 by date (6.77%). Rule: compare dates
- order_item_id is a counter, not an item ID. Problem: counting distinct values counts nothing useful. Evidence: 21 distinct values across 112,650 rows. Rule: it is only unique together with order_id
- Multi-seller orders. Problem: seller order counts overlap and a review or delivery counts for every seller in the order. Evidence: 1,278 multi-seller orders, seller order counts sum to 97,819 against 96,478. Rule: seller analysis is at item level and order counts are never added up
- Translation join. Problem: an inner join to the translation table drops items. Evidence: 1.33% of delivered revenue has no English name. Rule: left join and group missing categories as 'unknown'
- Same city name in several states. Problem: grouping by city name alone merges different cities. Evidence: Santa Maria exists in 4 states, Belem in 3. Rule: use city + state
- Same-day second orders. Problem: they inflate the repeat rate. Evidence: 829 customers placed a second order the same day (3.00% against 2.16%). Rule: repeat means at least 1 day after the first order
- Status strings are exact. Problem: 'canceled' is spelled with one l and a misspelling returns zero rows. Rule: copy the values from the status list above
- Gross numbers only. Problem: no refund, return, cost or commission data exists. Rule: revenue is what customers paid for items, not Olist's income, and profit cannot be computed
- Category names with source typos. Problem: the English names include typos, and a "corrected" name returns no rows. Evidence: home_confort, costruction_tools_garden, costruction_tools_tools, fashio_female_clothing, arts_and_craftmanship, plus lookalike pairs such as home_confort and home_comfort_2. Rule: use the names exactly as in the data and never merge them

## 6. Out of scope for version 1, and unknowns
- Out of scope (no defined metric or planned question uses them): distance between seller and customer, review comment text (Portuguese), product weight and dimensions, payment type and installments analysis
- Unknown and not answerable from the data: why daily volume falls away after 2018-08-21, and whether shipped orders were delivered
- Processing orders (301) were not checked by month
- Geolocation has 1,000,163 rows for 19,015 zip prefixes (52.6 rows per prefix). Not used, because joining it would multiply rows about 53 times
- Processing orders over time: [fill in from the month query]. Shipped orders appear in every month from 2016-09 to 2018-09