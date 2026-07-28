with combined as (
    select
        date,
        area_id,
        temperature_min_c,
        temperature_max_c,
        precipitation_mm,
        weather_code,
        loaded_at,
        pipeline_run_id,
        1 as priority
    from {{ ref('stg_weather_day') }}
    union all
    select
        date,
        area_id,
        temperature_min_c,
        temperature_max_c,
        precipitation_mm,
        weather_code,
        now() as loaded_at,
        'weather-seed'::text as pipeline_run_id,
        2 as priority
    from {{ ref('weather_day') }}
),
ranked as (
    select
        *,
        row_number() over (partition by date, area_id order by priority) as row_number
    from combined
)
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
from ranked
where row_number = 1
