with service_counts as (
    select
        source_snapshot_date,
        pipeline_run_id,
        service_id,
        count(*)::bigint as active_days
    from {{ ref('service_day') }}
    group by source_snapshot_date, pipeline_run_id, service_id
),
weighted_stop_calls as (
    select
        stop_times.source_snapshot_date as snapshot_date,
        routes.route_sk,
        stops.stop_sk,
        service.active_days,
        stop_times.loaded_at,
        stop_times.pipeline_run_id
    from {{ ref('stg_stop_times') }} as stop_times
    join {{ ref('stg_trips') }} as trips
      using (source_snapshot_date, pipeline_run_id, trip_id)
    join service_counts as service
      using (source_snapshot_date, pipeline_run_id, service_id)
    join {{ ref('dim_route_history') }} as routes
      on trips.route_id = routes.route_id
     and trips.source_snapshot_date between routes.valid_from and coalesce(routes.valid_to, '9999-12-31')
    join {{ ref('dim_stop_history') }} as stops
      on stop_times.stop_id = stops.stop_id
     and stop_times.source_snapshot_date between stops.valid_from and coalesce(stops.valid_to, '9999-12-31')
)
select
    md5(snapshot_date::text || '|' || route_sk || '|' || stop_sk) as stop_activity_sk,
    snapshot_date,
    route_sk,
    stop_sk,
    sum(active_days)::bigint as stop_events,
    min(loaded_at) as loaded_at,
    min(pipeline_run_id) as pipeline_run_id,
    snapshot_date as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from weighted_stop_calls
group by snapshot_date, route_sk, stop_sk
