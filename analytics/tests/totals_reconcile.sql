with totals as (
    select 'staging' as layer, count(*) as n, coalesce(sum(notional_usd),0) as volume, coalesce(sum(fee_usd),0) as fees
    from {{ ref('stg_trades') }}
    union all
    select 'fact',count(*),coalesce(sum(notional_usd),0),coalesce(sum(fee_usd),0) from {{ ref('fact_trades') }}
    union all
    select 'daily',coalesce(sum(trade_count),0),coalesce(sum(trade_volume_usd),0),coalesce(sum(fees_usd),0) from {{ ref('mart_daily_trading') }}
    union all
    select 'customer',coalesce(sum(total_trades),0),coalesce(sum(total_volume_usd),0),coalesce(sum(total_fee_usd),0) from {{ ref('mart_customer_summary') }}
)
select t.* from totals t cross join (select * from totals where layer='staging') s
where t.n<>s.n or t.volume<>s.volume or t.fees<>s.fees
