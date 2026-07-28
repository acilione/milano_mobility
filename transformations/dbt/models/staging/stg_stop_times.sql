select source.*
from {{ source('gtfs', 'stop_times') }} as source
join {{ source('audit', 'ingestion_manifest') }} as manifest
  using (pipeline_run_id)
where manifest.status in ('VALIDATED', 'PUBLISHED')
