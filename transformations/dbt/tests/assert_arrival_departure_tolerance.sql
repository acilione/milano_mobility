select *
from {{ ref('fact_stop_event') }}
where arrival_seconds > departure_seconds + 300
