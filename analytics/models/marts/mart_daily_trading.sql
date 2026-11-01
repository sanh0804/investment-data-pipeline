select
    dataset_id,
    trade_date,
    {{ cutoff_date() }} as as_of_date,
    count(*) as trade_count,
    count(distinct customer_id) as active_customers,
    sum(notional_usd) as trade_volume_usd,
    sum(fee_usd) as fees_usd
from {{ ref('fact_trades') }}
group by dataset_id, trade_date
