select
    t.*,
    c.created_at as customer_created_at,
    a.base_asset,
    a.quote_currency,
    d.date_day as calendar_date,
    p.close_price as market_close_price
from {{ ref('stg_trades') }} t
left join {{ ref('stg_customers') }} c
    on t.dataset_id = c.dataset_id and t.customer_id = c.customer_id
left join {{ ref('dim_assets') }} a
    on t.source = a.source and t.product_id = a.product_id
left join {{ ref('dim_date') }} d on t.trade_date = d.date_day
left join {{ ref('stg_asset_prices') }} p
    on t.source = p.source and t.product_id = p.product_id and t.price_date = p.price_date
