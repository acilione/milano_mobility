{{ config(materialized='view') }}

select
    effective_snapshot_date as snapshot_date,
    pipeline_run_id,
    retrieved_at
from {{ source('audit', 'ingestion_manifest') }}
where status = 'PUBLISHED'
order by effective_snapshot_date desc, retrieved_at desc
limit 1
