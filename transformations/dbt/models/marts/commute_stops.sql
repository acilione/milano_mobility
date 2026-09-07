{{ config(materialized='view') }}
select stops.stop_id, stops.stop_name, stops.stop_lat, stops.stop_lon
from {{ ref('stg_stops') }} as stops
join {{ ref('published_snapshot') }} as published using (pipeline_run_id)
where coalesce(stops.location_type, 0) = 0
