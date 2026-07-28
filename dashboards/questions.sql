-- Card 1: first-stop departures by route and service hour.
select
    trip.service_date,
    route.route_short_name,
    floor(trip.departure_seconds / 3600) as service_hour,
    count(*) as scheduled_departures
from marts.fact_scheduled_trip as trip
join marts.dim_route as route using (route_sk)
group by 1, 2, 3
order by 1, 2, 3;

-- Card 2: scheduled stop coverage by route.
select
    event.snapshot_date,
    route.route_short_name,
    count(distinct stop.stop_id) as served_stops
from marts.fact_stop_event as event
join marts.dim_route as route using (route_sk)
join marts.dim_stop as stop using (stop_sk)
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
    count(distinct trip.scheduled_trip_sk) as scheduled_trips
from marts.dim_date as date
left join marts.dim_weather_day as weather using (date_key)
left join marts.fact_scheduled_trip as trip using (date_key)
group by 1, 2, 3, 4, 5
order by 1;
