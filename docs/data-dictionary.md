# Data dictionary

## Audit

### `audit.ingestion_manifest`

One attempted source payload. `source + sha256` is the idempotency key enforced by the
application. Status moves through `RECEIVED`, `VALIDATED`, and `PUBLISHED`, or ends at
`QUARANTINED`. HTTP ETag and Last-Modified are discovery hints; SHA-256 is authoritative.

## Core history

### `core.dim_stop_history`

One type-2 version per `stop_id`. `stop_sk` is derived from the natural ID and
`valid_from`. `valid_to` is inclusive; null means current. Position and descriptive
attributes contribute to `entity_hash`.

### `core.dim_route_history`

One type-2 version per `route_id`, with the same validity convention as stop history.
Includes agency, public names, GTFS route type, and display colors.

### `core.service_day`

One active `service_id` per source snapshot and `service_date`. Weekly `calendar.txt`
rules are expanded and then adjusted by `calendar_dates.txt` additions and removals.

## Marts

### `marts.dim_date`

One date between the first and last active service day. `date_key` uses `YYYYMMDD`.
Includes ISO weekday, weekend flag, and meteorological season.

### `marts.dim_weather_day`

One `date_key + area_id`. Temperatures are Celsius and precipitation is millimetres.
Weather code follows the source provider contract.

### `marts.fact_scheduled_trip`

One `snapshot_date + service_date + trip_id`. Includes the historical route surrogate,
direction, and first-stop departure in service-day seconds. This complete logical fact is
exposed as a view so official service-day expansion is not duplicated on local disk.

### `marts.fact_stop_event`

One `snapshot_date + service_date + trip_id + stop_sequence`. Arrival and departure are
integer seconds after the start of the service day and may exceed 86,400. This is also a
complete logical view; it is not a sample or row-limited model.

### `marts.dashboard_service_activity`

One `snapshot_date + service_date + service_hour + route_sk`. `scheduled_trips` is the
number of first-stop departures in the bucket. Service hours can exceed 23 for trips
after midnight belonging to the previous GTFS service day.

### `marts.dashboard_stop_activity`

One `snapshot_date + route_sk + stop_sk`. `stop_events` is the full count of scheduled
calls across the snapshot's active service window. The model weights each source stop
call by its service's active-day count, producing the same total as the logical stop-event
fact without first materializing every service-day row.

### `marts.fact_network_change`

One changed entity between consecutive snapshots. `entity_type` is `stop`, `route`, or
`trip`; `change_type` is `ADDED`, `REMOVED`, or `MODIFIED`. Old/new hashes make the result
auditable without duplicating wide source rows.

### Commute routing tables

`marts.commute_connections` stores one adjacent pair of calls per source snapshot,
pipeline run, trip and stop sequence, including departure/arrival seconds, service ID,
route name, and ordinary boarding/alighting permissions. It is indexed by pipeline run,
service ID and departure time. It does not expand trips across every service date.

`marts.commute_service` exposes the exception-adjusted service calendar to the BI role.
`marts.commute_stops` exposes boarding stops for the currently published pipeline run.
The API reads a consistent snapshot and uses calendar-day offsets for extended GTFS hours.

Commute results contain a latest departure and ordered walking/transit legs per stop.
`minutes` includes time until the selected arrival deadline (including any arrival margin).
Leg times are seconds relative to midnight on the selected date; negative times belong
to the previous calendar day. Walking areas are estimates, not verified pedestrian routes.

## Universal audit fields

Analytical gold tables expose:

- `loaded_at`: source staging load timestamp or transformation timestamp.
- `pipeline_run_id`: ingestion run that introduced the source record.
- `source_snapshot_date`: source version from which the record derives.
- `dbt_invocation_id`: exact dbt execution that produced the model.
