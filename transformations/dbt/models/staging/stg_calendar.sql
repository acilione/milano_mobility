select source.*
from {{ source('gtfs', 'calendar') }} as source
join {{ source('audit', 'ingestion_manifest') }} as manifest
  using (pipeline_run_id)
where manifest.status in ('VALIDATED', 'PUBLISHED')
