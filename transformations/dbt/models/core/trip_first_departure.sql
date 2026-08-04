select distinct on (source_snapshot_date, pipeline_run_id, trip_id)
    source_snapshot_date,
    pipeline_run_id,
    trip_id,
    departure_seconds,
    loaded_at
from {{ ref('stg_stop_times') }}
order by source_snapshot_date, pipeline_run_id, trip_id, stop_sequence
