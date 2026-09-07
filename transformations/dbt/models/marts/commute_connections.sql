{{ config(
    indexes=[{'columns': ['pipeline_run_id', 'service_id', 'departure_seconds']}],
    pre_hook=['set local enable_nestloop = off', 'set local enable_mergejoin = off']
) }}

-- Keep adjacent calls together before filtering by a requested travel window.
with calls as (
    select
        source_snapshot_date, pipeline_run_id, trip_id, stop_sequence,
        stop_id as from_stop, departure_seconds,
        coalesce(pickup_type, 0) = 0 as can_board,
        lead(stop_id) over trip as to_stop,
        lead(arrival_seconds) over trip as arrival_seconds,
        coalesce(lead(drop_off_type) over trip, 0) = 0 as can_alight
    from {{ ref('stg_stop_times') }}
    window trip as (
        partition by source_snapshot_date, pipeline_run_id, trip_id order by stop_sequence
    )
),
-- Materialize the small trip/route join before joining millions of stop pairs.
-- The audit-status views can otherwise cause severe cardinality underestimates.
trip_routes as materialized (
    select trips.source_snapshot_date, trips.pipeline_run_id, trips.trip_id, trips.service_id,
        coalesce(routes.route_short_name, routes.route_id) as route_name
    from {{ ref('stg_trips') }} as trips
    join {{ ref('stg_routes') }} as routes using (source_snapshot_date, pipeline_run_id, route_id)
)
select calls.*, trip_routes.service_id, trip_routes.route_name
from calls
join trip_routes using (source_snapshot_date, pipeline_run_id, trip_id)
where to_stop is not null and arrival_seconds >= departure_seconds
