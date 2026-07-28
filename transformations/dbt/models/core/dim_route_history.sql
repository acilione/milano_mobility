with ordered as (
    select
        *,
        case
            when entity_hash = lag(entity_hash) over (
                partition by route_id order by source_snapshot_date
            ) then 0
            else 1
        end as is_new_version
    from {{ ref('stg_routes') }}
),
versioned as (
    select
        *,
        sum(is_new_version) over (
            partition by route_id order by source_snapshot_date
            rows between unbounded preceding and current row
        ) as version_number
    from ordered
),
collapsed as (
    select
        route_id,
        version_number,
        min(source_snapshot_date) as valid_from,
        min(agency_id) as agency_id,
        min(route_short_name) as route_short_name,
        min(route_long_name) as route_long_name,
        min(route_desc) as route_desc,
        min(route_type) as route_type,
        min(route_color) as route_color,
        min(route_text_color) as route_text_color,
        min(entity_hash) as entity_hash,
        min(pipeline_run_id) as pipeline_run_id,
        min(loaded_at) as loaded_at
    from versioned
    group by route_id, version_number
),
bounded as (
    select
        *,
        lead(valid_from) over (partition by route_id order by valid_from) - 1 as valid_to
    from collapsed
)
select
    md5(route_id || '|' || valid_from::text) as route_sk,
    route_id,
    agency_id,
    route_short_name,
    route_long_name,
    route_desc,
    route_type,
    route_color,
    route_text_color,
    valid_from,
    valid_to,
    valid_to is null as is_current,
    entity_hash,
    loaded_at,
    pipeline_run_id,
    valid_from as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from bounded
