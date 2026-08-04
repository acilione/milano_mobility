{{ config(materialized='view') }}

select
    md5(
        trips.source_snapshot_date::text || '|' || service.service_date::text || '|' || trips.trip_id
    ) as scheduled_trip_sk,
    trips.source_snapshot_date as snapshot_date,
    service.service_date,
    to_char(service.service_date, 'YYYYMMDD')::integer as date_key,
    trips.trip_id,
    trips.service_id,
    routes.route_sk,
    trips.direction_id,
    first_departure.departure_seconds,
    trips.loaded_at,
    trips.pipeline_run_id,
    trips.source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from {{ ref('stg_trips') }} as trips
join {{ ref('service_day') }} as service
  using (source_snapshot_date, pipeline_run_id, service_id)
join {{ ref('dim_route_history') }} as routes
  on trips.route_id = routes.route_id
 and trips.source_snapshot_date between routes.valid_from and coalesce(routes.valid_to, '9999-12-31')
left join {{ ref('trip_first_departure') }} as first_departure
  on trips.source_snapshot_date = first_departure.source_snapshot_date
 and trips.pipeline_run_id = first_departure.pipeline_run_id
 and trips.trip_id = first_departure.trip_id
