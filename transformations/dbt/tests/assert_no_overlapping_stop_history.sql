select current_version.stop_id
from {{ ref('dim_stop_history') }} as current_version
join {{ ref('dim_stop_history') }} as other_version
  on current_version.stop_id = other_version.stop_id
 and current_version.stop_sk < other_version.stop_sk
 and current_version.valid_from <= coalesce(other_version.valid_to, '9999-12-31')
 and other_version.valid_from <= coalesce(current_version.valid_to, '9999-12-31')
