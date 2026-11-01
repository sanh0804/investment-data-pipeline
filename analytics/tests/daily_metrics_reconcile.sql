with expected as (
    select dataset_id,trade_date,count(*) as n,count(distinct customer_id) as customers,
           sum(notional_usd) as volume,sum(fee_usd) as fees
    from {{ ref('stg_trades') }} group by dataset_id,trade_date
)
select coalesce(e.trade_date,m.trade_date) as failing_day
from expected e full join {{ ref('mart_daily_trading') }} m
on e.dataset_id=m.dataset_id and e.trade_date=m.trade_date
where e.trade_date is null or m.trade_date is null
   or row(e.n,e.customers,e.volume,e.fees) is distinct from row(m.trade_count,m.active_customers,m.trade_volume_usd,m.fees_usd)
