select
    dataset_id,
    customer_id,
    {{ key(['dataset_id', 'customer_id']) }} as customer_key,
    customer_number,
    created_at,
    (created_at at time zone 'UTC')::date as created_date
from {{ source('synthetic_input', 'customers') }}
where dataset_id = {{ literal(var('dataset_id')) }}
  and created_at < (({{ cutoff_date() }} + 1)::timestamp at time zone 'UTC')
