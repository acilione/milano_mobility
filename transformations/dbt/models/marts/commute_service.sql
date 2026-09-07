{{ config(materialized='view') }}
select source_snapshot_date, pipeline_run_id, service_id, service_date
from {{ ref('service_day') }}
