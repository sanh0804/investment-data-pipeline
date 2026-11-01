select
    source,
    product_id,
    price_date,
    {{ key(['source', 'product_id', 'price_date']) }} as price_key,
    {{ key(['source', 'product_id']) }} as asset_key,
    close_price
from {{ source('market_input', 'daily_prices') }}
where source = {{ literal(var('price_source')) }}
  and price_date <= {{ cutoff_date() }}
