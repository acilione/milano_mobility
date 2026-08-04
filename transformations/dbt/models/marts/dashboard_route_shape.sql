with route_shapes as (
    select distinct
        trips.source_snapshot_date as snapshot_date,
        trips.pipeline_run_id,
        routes.route_sk,
        trips.shape_id
    from {{ ref('stg_trips') }} as trips
    join {{ ref('dim_route_history') }} as routes
      on trips.route_id = routes.route_id
     and trips.source_snapshot_date between routes.valid_from and coalesce(routes.valid_to, '9999-12-31')
    where trips.shape_id is not null
)
select
    md5(
        shapes.source_snapshot_date::text
        || '|' || route_shapes.route_sk
        || '|' || shapes.shape_id
        || '|' || shapes.shape_pt_sequence::text
    ) as route_shape_point_sk,
    shapes.source_snapshot_date as snapshot_date,
    route_shapes.route_sk,
    shapes.shape_id,
    shapes.shape_pt_sequence,
    shapes.shape_pt_lat,
    shapes.shape_pt_lon,
    shapes.shape_dist_traveled,
    shapes.loaded_at,
    shapes.pipeline_run_id,
    shapes.source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from {{ ref('stg_shapes') }} as shapes
join route_shapes
  on shapes.source_snapshot_date = route_shapes.snapshot_date
 and shapes.pipeline_run_id = route_shapes.pipeline_run_id
 and shapes.shape_id = route_shapes.shape_id
