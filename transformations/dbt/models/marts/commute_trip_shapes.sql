{{ config(indexes=[{'columns': ['pipeline_run_id', 'trip_id']}]) }}

select source_snapshot_date, pipeline_run_id, trip_id, shape_id
from {{ ref('stg_trips') }}
where shape_id is not null
