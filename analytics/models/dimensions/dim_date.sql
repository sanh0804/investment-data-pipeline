with bounds as (
    select coalesce(min(day), {{ cutoff_date() }}) as start_date from (
        select created_date as day from {{ ref('stg_customers') }}
        union all
        select trade_date as day from {{ ref('stg_trades') }}
    ) dates
)
select
    d::date as date_day,
    extract(year from d)::int as year_number,
    extract(quarter from d)::int as quarter_number,
    extract(month from d)::int as month_number,
    extract(day from d)::int as day_of_month,
    extract(isodow from d)::int as iso_day_of_week,
    extract(isodow from d) in (6, 7) as is_weekend,
    date_trunc('month', d)::date as month_start
from bounds
cross join lateral generate_series(start_date::timestamp, {{ cutoff_date() }}::timestamp, interval '1 day') as d
