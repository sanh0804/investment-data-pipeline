select distinct
    asset_key,
    source,
    product_id,
    split_part(product_id, '-', 1) as base_asset,
    split_part(product_id, '-', 2) as quote_currency
from {{ ref('stg_asset_prices') }}
