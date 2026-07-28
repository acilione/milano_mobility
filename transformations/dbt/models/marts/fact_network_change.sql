with snapshots as (
    select
        snapshot_date,
        lag(snapshot_date) over (order by snapshot_date) as previous_snapshot_date
    from (
        select distinct effective_snapshot_date as snapshot_date
        from {{ source('audit', 'ingestion_manifest') }}
        where status in ('VALIDATED', 'PUBLISHED')
    ) as accepted_snapshots
),
stop_changes as (
    with entity_keys as (
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, current_entity.stop_id
        from snapshots
        join {{ ref('stg_stops') }} as current_entity
          on current_entity.source_snapshot_date = snapshots.snapshot_date
        union
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, previous_entity.stop_id
        from snapshots
        join {{ ref('stg_stops') }} as previous_entity
          on previous_entity.source_snapshot_date = snapshots.previous_snapshot_date
    )
    select
        entity_keys.snapshot_date,
        entity_keys.previous_snapshot_date,
        'stop'::text as entity_type,
        entity_keys.stop_id as entity_id,
        previous_entity.entity_hash as old_hash,
        current_entity.entity_hash as new_hash,
        coalesce(current_entity.pipeline_run_id, previous_entity.pipeline_run_id) as pipeline_run_id,
        coalesce(current_entity.loaded_at, previous_entity.loaded_at) as loaded_at
    from entity_keys
    left join {{ ref('stg_stops') }} as current_entity
      on current_entity.source_snapshot_date = entity_keys.snapshot_date
     and current_entity.stop_id = entity_keys.stop_id
    left join {{ ref('stg_stops') }} as previous_entity
      on previous_entity.source_snapshot_date = entity_keys.previous_snapshot_date
     and previous_entity.stop_id = entity_keys.stop_id
),
route_changes as (
    with entity_keys as (
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, current_entity.route_id
        from snapshots
        join {{ ref('stg_routes') }} as current_entity
          on current_entity.source_snapshot_date = snapshots.snapshot_date
        union
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, previous_entity.route_id
        from snapshots
        join {{ ref('stg_routes') }} as previous_entity
          on previous_entity.source_snapshot_date = snapshots.previous_snapshot_date
    )
    select
        entity_keys.snapshot_date,
        entity_keys.previous_snapshot_date,
        'route'::text as entity_type,
        entity_keys.route_id as entity_id,
        previous_entity.entity_hash as old_hash,
        current_entity.entity_hash as new_hash,
        coalesce(current_entity.pipeline_run_id, previous_entity.pipeline_run_id) as pipeline_run_id,
        coalesce(current_entity.loaded_at, previous_entity.loaded_at) as loaded_at
    from entity_keys
    left join {{ ref('stg_routes') }} as current_entity
      on current_entity.source_snapshot_date = entity_keys.snapshot_date
     and current_entity.route_id = entity_keys.route_id
    left join {{ ref('stg_routes') }} as previous_entity
      on previous_entity.source_snapshot_date = entity_keys.previous_snapshot_date
     and previous_entity.route_id = entity_keys.route_id
),
trip_changes as (
    with entity_keys as (
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, current_entity.trip_id
        from snapshots
        join {{ ref('stg_trips') }} as current_entity
          on current_entity.source_snapshot_date = snapshots.snapshot_date
        union
        select snapshots.snapshot_date, snapshots.previous_snapshot_date, previous_entity.trip_id
        from snapshots
        join {{ ref('stg_trips') }} as previous_entity
          on previous_entity.source_snapshot_date = snapshots.previous_snapshot_date
    )
    select
        entity_keys.snapshot_date,
        entity_keys.previous_snapshot_date,
        'trip'::text as entity_type,
        entity_keys.trip_id as entity_id,
        previous_entity.entity_hash as old_hash,
        current_entity.entity_hash as new_hash,
        coalesce(current_entity.pipeline_run_id, previous_entity.pipeline_run_id) as pipeline_run_id,
        coalesce(current_entity.loaded_at, previous_entity.loaded_at) as loaded_at
    from entity_keys
    left join {{ ref('stg_trips') }} as current_entity
      on current_entity.source_snapshot_date = entity_keys.snapshot_date
     and current_entity.trip_id = entity_keys.trip_id
    left join {{ ref('stg_trips') }} as previous_entity
      on previous_entity.source_snapshot_date = entity_keys.previous_snapshot_date
     and previous_entity.trip_id = entity_keys.trip_id
),
all_changes as (
    select * from stop_changes
    union all
    select * from route_changes
    union all
    select * from trip_changes
)
select
    md5(snapshot_date::text || '|' || entity_type || '|' || entity_id) as network_change_sk,
    snapshot_date,
    previous_snapshot_date,
    entity_type,
    entity_id,
    case
        when old_hash is null then 'ADDED'
        when new_hash is null then 'REMOVED'
        else 'MODIFIED'
    end as change_type,
    old_hash,
    new_hash,
    loaded_at,
    pipeline_run_id,
    snapshot_date as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from all_changes
where old_hash is distinct from new_hash
