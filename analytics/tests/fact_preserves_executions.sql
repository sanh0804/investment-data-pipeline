select coalesce(s.trade_key, f.trade_key) as failing_trade_key
from {{ ref('stg_trades') }} s
full join {{ ref('fact_trades') }} f on s.trade_key = f.trade_key
where s.trade_key is null or f.trade_key is null
   or row(s.dataset_id,s.customer_id,s.source,s.product_id,s.trade_date,s.side,s.quantity,s.execution_price,s.notional_usd,s.fee_usd)
      is distinct from row(f.dataset_id,f.customer_id,f.source,f.product_id,f.trade_date,f.side,f.quantity,f.execution_price,f.notional_usd,f.fee_usd)
