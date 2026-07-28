select current_version.route_id
from {{ ref('dim_route_history') }} as current_version
join {{ ref('dim_route_history') }} as other_version
  on current_version.route_id = other_version.route_id
 and current_version.route_sk < other_version.route_sk
 and current_version.valid_from <= coalesce(other_version.valid_to, '9999-12-31')
 and other_version.valid_from <= coalesce(current_version.valid_to, '9999-12-31')
