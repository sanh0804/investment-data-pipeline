with activity as (
    select
        dataset_id,
        customer_id,
        min(trade_date) as first_trade_date,
        max(trade_date) as last_trade_date,
        count(*) as total_trades,
        sum(notional_usd) as total_volume_usd,
        sum(fee_usd) as total_fee_usd
    from {{ ref('fact_trades') }}
    group by dataset_id, customer_id
)
select
    c.dataset_id,
    c.customer_id,
    c.customer_key,
    c.created_at,
    {{ cutoff_date() }} as as_of_date,
    a.first_trade_date,
    a.last_trade_date,
    {{ cutoff_date() }} - a.last_trade_date as days_since_last_trade,
    coalesce(a.total_trades, 0) as total_trades,
    coalesce(a.total_volume_usd, 0::numeric) as total_volume_usd,
    coalesce(a.total_fee_usd, 0::numeric) as total_fee_usd,
    case
        when a.total_trades is null then 'never_traded'
        when {{ cutoff_date() }} - a.last_trade_date <= 30 then 'active'
        when {{ cutoff_date() }} - a.last_trade_date < 60 then 'at_risk'
        else 'inactive'
    end as activity_segment
from {{ ref('stg_customers') }} c
left join activity a on c.dataset_id = a.dataset_id and c.customer_id = a.customer_id
