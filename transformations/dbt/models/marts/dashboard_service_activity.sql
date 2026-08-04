with bucketed as (
    select
        *,
        floor(departure_seconds / 3600)::integer as service_hour
    from {{ ref('fact_scheduled_trip') }}
)
select
    md5(
        snapshot_date::text || '|' || service_date::text || '|'
        || coalesce(service_hour::text, 'unknown') || '|' || route_sk
    ) as service_activity_sk,
    snapshot_date,
    service_date,
    date_key,
    service_hour,
    route_sk,
    count(*) as scheduled_trips,
    min(loaded_at) as loaded_at,
    min(pipeline_run_id) as pipeline_run_id,
    snapshot_date as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from bucketed
group by snapshot_date, service_date, date_key, service_hour, route_sk
