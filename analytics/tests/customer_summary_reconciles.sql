with expected as (
    select c.dataset_id,c.customer_id,count(t.trade_id) as n,
        min(t.trade_date) as first_day,max(t.trade_date) as last_day,
        coalesce(sum(t.notional_usd),0) as volume,coalesce(sum(t.fee_usd),0) as fees
    from {{ ref('stg_customers') }} c
    left join {{ ref('stg_trades') }} t on c.dataset_id=t.dataset_id and c.customer_id=t.customer_id
    group by c.dataset_id,c.customer_id
)
select coalesce(e.customer_id,m.customer_id) as failing_customer
from expected e full join {{ ref('mart_customer_summary') }} m
on e.dataset_id=m.dataset_id and e.customer_id=m.customer_id
where e.customer_id is null or m.customer_id is null
   or row(e.n,e.first_day,e.last_day,e.volume,e.fees,{{ cutoff_date() }}-e.last_day)
      is distinct from row(m.total_trades,m.first_trade_date,m.last_trade_date,m.total_volume_usd,m.total_fee_usd,m.days_since_last_trade)
   or m.activity_segment <> case when e.n=0 then 'never_traded'
        when {{ cutoff_date() }}-e.last_day<=30 then 'active'
        when {{ cutoff_date() }}-e.last_day<60 then 'at_risk' else 'inactive' end
