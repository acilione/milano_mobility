select
    weather_date as date,
    area_id,
    temperature_min_c,
    temperature_max_c,
    precipitation_mm,
    weather_code,
    source_uri,
    pipeline_run_id,
    retrieved_at as loaded_at
from {{ source('gtfs', 'weather_day') }}
