select
    d.product_id,
    p.product_category_name,
    d.product_category_name_pt,
    d.product_category_name_en
from {{ ref('dim_products') }} d
join {{ ref('stg_products') }} p on p.product_id = d.product_id
where (p.product_category_name is not null and d.product_category_name_pt <> p.product_category_name)
   or (p.product_category_name is not null and d.product_category_name_en = 'unknown')
   or (p.product_category_name is null and d.product_category_name_en <> 'unknown')