select *
from {{ ref('stg_stop_times') }}
where arrival_seconds > departure_seconds + 300
