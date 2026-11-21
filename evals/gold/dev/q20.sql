/*
Q20: What share of delivered revenue comes from the top 10 sellers?
Top 10 sellers by delivered revenue (sum of item price). Share in percent of total delivered revenue.
No tie at rank 10 (rank 10 and rank 11 differ by about 9,000).
*/

WITH seller_revenue AS (
    SELECT
        oi.seller_id,
        SUM(oi.price) AS revenue
    FROM raw.order_items oi
    JOIN raw.orders o
        ON oi.order_id = o.order_id
    WHERE o.order_status = 'delivered'
    GROUP BY oi.seller_id
),

ranked AS (
    SELECT
        seller_id,
        revenue,
        ROW_NUMBER() OVER (ORDER BY revenue DESC) AS rnk
    FROM seller_revenue
)

SELECT
    ROUND(100.0 * SUM(revenue) FILTER (WHERE rnk <= 10) / SUM(revenue), 2) AS top10_revenue_share_pct
FROM ranked;
