{{ config(materialized='view') }}

select
    md5(
        stop_times.source_snapshot_date::text || '|' || service.service_date::text || '|'
        || stop_times.trip_id || '|' || stop_times.stop_sequence::text
    ) as stop_event_sk,
    stop_times.source_snapshot_date as snapshot_date,
    service.service_date,
    to_char(service.service_date, 'YYYYMMDD')::integer as date_key,
    stop_times.trip_id,
    routes.route_sk,
    stops.stop_sk,
    stop_times.stop_sequence,
    stop_times.arrival_seconds,
    stop_times.departure_seconds,
    stop_times.loaded_at,
    stop_times.pipeline_run_id,
    stop_times.source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from {{ ref('stg_stop_times') }} as stop_times
join {{ ref('stg_trips') }} as trips
  using (source_snapshot_date, pipeline_run_id, trip_id)
join {{ ref('service_day') }} as service
  using (source_snapshot_date, pipeline_run_id, service_id)
join {{ ref('dim_route_history') }} as routes
  on trips.route_id = routes.route_id
 and trips.source_snapshot_date between routes.valid_from and coalesce(routes.valid_to, '9999-12-31')
join {{ ref('dim_stop_history') }} as stops
  on stop_times.stop_id = stops.stop_id
 and stop_times.source_snapshot_date between stops.valid_from and coalesce(stops.valid_to, '9999-12-31')
