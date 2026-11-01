select
    dataset_id,
    trade_id,
    {{ key(['dataset_id', 'trade_id']) }} as trade_key,
    customer_id,
    {{ key(['dataset_id', 'customer_id']) }} as customer_key,
    source,
    product_id,
    {{ key(['source', 'product_id']) }} as asset_key,
    {{ key(['source', 'product_id', 'price_date']) }} as price_key,
    traded_at,
    (traded_at at time zone 'UTC')::date as trade_date,
    price_date,
    side,
    status,
    quantity,
    reference_close,
    slippage_bps,
    execution_price,
    notional_usd,
    fee_usd
from {{ source('synthetic_input', 'trades') }}
where dataset_id = {{ literal(var('dataset_id')) }}
  and traded_at < (({{ cutoff_date() }} + 1)::timestamp at time zone 'UTC')
