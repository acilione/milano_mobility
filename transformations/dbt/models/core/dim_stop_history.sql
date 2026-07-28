with ordered as (
    select
        *,
        case
            when entity_hash = lag(entity_hash) over (
                partition by stop_id order by source_snapshot_date
            ) then 0
            else 1
        end as is_new_version
    from {{ ref('stg_stops') }}
),
versioned as (
    select
        *,
        sum(is_new_version) over (
            partition by stop_id order by source_snapshot_date
            rows between unbounded preceding and current row
        ) as version_number
    from ordered
),
collapsed as (
    select
        stop_id,
        version_number,
        min(source_snapshot_date) as valid_from,
        min(stop_code) as stop_code,
        min(stop_name) as stop_name,
        min(stop_desc) as stop_desc,
        min(stop_lat) as stop_lat,
        min(stop_lon) as stop_lon,
        min(zone_id) as zone_id,
        min(entity_hash) as entity_hash,
        min(pipeline_run_id) as pipeline_run_id,
        min(loaded_at) as loaded_at
    from versioned
    group by stop_id, version_number
),
bounded as (
    select
        *,
        lead(valid_from) over (partition by stop_id order by valid_from) - 1 as valid_to
    from collapsed
)
select
    md5(stop_id || '|' || valid_from::text) as stop_sk,
    stop_id,
    stop_code,
    stop_name,
    stop_desc,
    stop_lat,
    stop_lon,
    zone_id,
    valid_from,
    valid_to,
    valid_to is null as is_current,
    entity_hash,
    loaded_at,
    pipeline_run_id,
    valid_from as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from bounded
