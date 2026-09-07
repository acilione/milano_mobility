-- An adjacent connection must move forward through a trip's ordered calls.
select pipeline_run_id, trip_id, stop_sequence
from {{ ref('commute_connections') }}
where from_stop is null or to_stop is null or arrival_seconds < departure_seconds
union all
select pipeline_run_id, trip_id, stop_sequence
from {{ ref('commute_connections') }}
group by pipeline_run_id, trip_id, stop_sequence
having count(*) > 1
