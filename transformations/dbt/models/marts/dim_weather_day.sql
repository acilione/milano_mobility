select
    to_char(date, 'YYYYMMDD')::integer as date_key,
    date,
    area_id,
    temperature_min_c,
    temperature_max_c,
    precipitation_mm,
    weather_code,
    loaded_at,
    pipeline_run_id,
    (select max(source_snapshot_date) from {{ ref('service_day') }}) as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from {{ ref('stg_weather_day') }}
