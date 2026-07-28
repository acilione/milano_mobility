with bounds as (
    select min(service_date) as first_date, max(service_date) as last_date
    from {{ ref('service_day') }}
),
dates as (
    select generate_series(first_date, last_date, interval '1 day')::date as date
    from bounds
)
select
    to_char(dates.date, 'YYYYMMDD')::integer as date_key,
    dates.date,
    extract(isodow from dates.date)::smallint as iso_weekday,
    to_char(dates.date, 'FMDay') as weekday_name,
    extract(isodow from dates.date) in (6, 7) as is_weekend,
    holidays.holiday_name is not null as is_holiday,
    holidays.holiday_name,
    case
        when extract(month from dates.date) in (12, 1, 2) then 'winter'
        when extract(month from dates.date) in (3, 4, 5) then 'spring'
        when extract(month from dates.date) in (6, 7, 8) then 'summer'
        else 'autumn'
    end as season,
    now() as loaded_at,
    'dbt'::text as pipeline_run_id,
    (select max(source_snapshot_date) from {{ ref('service_day') }}) as source_snapshot_date,
    '{{ invocation_id }}'::text as dbt_invocation_id
from dates
left join {{ ref('holidays') }} as holidays using (date)
