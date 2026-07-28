with regular_service as (
    select
        calendar.source_snapshot_date,
        calendar.pipeline_run_id,
        calendar.loaded_at,
        calendar.service_id,
        dates.service_date
    from {{ ref('stg_calendar') }} as calendar
    cross join lateral generate_series(
        calendar.start_date, calendar.end_date, interval '1 day'
    ) as dates(service_date)
    where case extract(isodow from dates.service_date)
        when 1 then calendar.monday
        when 2 then calendar.tuesday
        when 3 then calendar.wednesday
        when 4 then calendar.thursday
        when 5 then calendar.friday
        when 6 then calendar.saturday
        when 7 then calendar.sunday
    end = 1
),
removed_service as (
    select source_snapshot_date, service_id, service_date
    from {{ ref('stg_calendar_dates') }}
    where exception_type = 2
),
added_service as (
    select
        source_snapshot_date,
        pipeline_run_id,
        loaded_at,
        service_id,
        service_date
    from {{ ref('stg_calendar_dates') }}
    where exception_type = 1
),
combined as (
    select regular_service.*
    from regular_service
    left join removed_service
      using (source_snapshot_date, service_id, service_date)
    where removed_service.service_id is null
    union
    select * from added_service
)
select
    source_snapshot_date,
    pipeline_run_id,
    loaded_at,
    service_id,
    service_date::date as service_date
from combined
