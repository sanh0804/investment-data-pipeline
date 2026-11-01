with source_counts as (
    select count(*) as n from {{ source('synthetic_input','trades') }}
    where dataset_id={{ literal(var('dataset_id')) }}
      and traded_at < (({{ cutoff_date() }}+1)::timestamp at time zone 'UTC')
), customer_counts as (
    select count(*) as n from {{ source('synthetic_input','customers') }}
    where dataset_id={{ literal(var('dataset_id')) }}
      and created_at < (({{ cutoff_date() }}+1)::timestamp at time zone 'UTC')
)
select 'trade_source_count' as issue where (select n from source_counts)<>(select count(*) from {{ ref('stg_trades') }})
union all
select 'customer_source_count' where (select n from customer_counts)<>(select count(*) from {{ ref('stg_customers') }})
union all
select 'invalid_money_or_date' from {{ ref('stg_trades') }}
where quantity<=0 or execution_price<=0 or fee_usd<0 or slippage_bps not between -25 and 25
   or price_date<>trade_date
   or execution_price<>round(reference_close*(1+slippage_bps::numeric/10000),2)
   or notional_usd<>round(quantity*execution_price,2) or fee_usd<>round(notional_usd*0.001,2)
union all
select 'invalid_customer_time' from {{ ref('fact_trades') }} where traded_at<=customer_created_at
union all
select 'invalid_price' from {{ ref('stg_asset_prices') }} where close_price<=0
