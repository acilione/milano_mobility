-- Card 1: first-stop departures by route and service hour.
select
    activity.service_date,
    route.route_short_name,
    activity.service_hour,
    sum(activity.scheduled_trips) as scheduled_departures
from marts.dashboard_service_activity as activity
join marts.dim_route as route using (route_sk)
group by 1, 2, 3
order by 1, 2, 3;

-- Card 2: scheduled stop coverage by route.
select
    activity.snapshot_date,
    route.route_short_name,
    count(*) as served_stops,
    sum(activity.stop_events) as scheduled_stop_events
from marts.dashboard_stop_activity as activity
join marts.dim_route as route using (route_sk)
group by 1, 2
order by 1, 2;

-- Card 3: changes between source snapshots.
select
    snapshot_date,
    entity_type,
    change_type,
    count(*) as changed_entities
from marts.fact_network_change
group by 1, 2, 3
order by 1, 2, 3;

-- Card 4: service-day schedule with optional weather context.
select
    date.date,
    date.weekday_name,
    date.is_holiday,
    weather.temperature_max_c,
    weather.precipitation_mm,
    coalesce(sum(activity.scheduled_trips), 0) as scheduled_trips
from marts.dim_date as date
left join marts.dim_weather_day as weather using (date_key)
left join marts.dashboard_service_activity as activity using (date_key)
group by 1, 2, 3, 4, 5
order by 1;
