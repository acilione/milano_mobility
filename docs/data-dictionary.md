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

`marts.commute_trip_shapes` maps source pipeline run and trip ID to the official shape
ID, indexed by run/trip. Journey maps join this to `dashboard_route_shape` and match
scheduled stops in travel order to display the boarded segment. Missing geometry is
explicitly labelled as a schematic connection rather than a verified street/track path.

### Address journey comparison API

`GET /api/places?q=...` returns up to five distinct Milan address candidates, using
a local bounding box and filtering the provider's municipality to Milan/Milano.
Address fields request suggestions after three characters and a 600 ms typing pause.
`POST /api/comparison`
accepts JSON with `destination: {name, point: [lon, lat]}`, `addresses` (one to three
objects with `name` and `point`), `week` (ISO date,
normalized to Monday), `days` (0 = Monday through 6 = Sunday), `arrival`, `departure`
(HH:MM in Europe/Rome), and `walk` (5, 10 or 15 minutes per leg). The request requires
`X-Mobility-Action: compare` and has a 16 KB size limit.

Each address contains per-date `outbound`, `return` and `return_later` journeys,
including `departure`, `arrival`, `seconds`, `walking_seconds`, `transfers` and ordered
`legs`. Times are seconds relative to midnight on that date. Journey duration includes
connection waits; return duration also includes waiting after the requested departure.
Unused arrival margin at the destination is excluded. `return_later` starts its search
ten minutes after the chosen return departure. `weekly_seconds` sums the selected
round trips only when every one is available; otherwise it is null. Uncovered dates
have `status: unavailable` and a reason. Covered dates may still have null journeys
when no route is found under the routing limits.

Street access/egress considers twelve nearby boarding stops per location. Transfers
retain estimated walks and a two-minute allowance, with at most three transit boardings.
The search is bounded to ninety minutes per direction. The API returns the source
snapshot date and service coverage; neither implies live vehicle observations.
Older `apartments` request/response fields remain supported for saved-link compatibility.
New clients use `addresses` and omit excluded addresses from requests. Leg `coordinates`
are ordered endpoints or scheduled stops; transit legs also include their source `trip_id`.
The response includes `pipeline_run_id` for map/snapshot consistency checks.

### Nearby places and route maps

`POST /api/nearby` accepts `addresses: [{name, point: [lon, lat]}]` (1–3), category IDs,
`radius` (500/1000/1500 metres), and `minutes` (5/10/15/20). Supported categories are
`cafe`, `supermarket`, `cinema`, `pharmacy`, `restaurant`, `park`, `post_office`, `bank`,
`healthcare`, and `gym`. Supermarkets include convenience shops; banks include ATMs.
Overpass discovery is cached by point/radius/UTC day. Private/no-access entries and
polygon centres outside the search radius are excluded.

Each address has `status: ready` or `unavailable`. For each selected category:

- `mapped_count`: mapped candidates within the search radius.
- `checked_count`: up to 20 nearest candidates checked using pedestrian street routing.
- `sampled`: true when more candidates were mapped than checked.
- `reachable_count`: checked candidates with a verified route within the walking limit.
- `unknown_count`: checked candidates with no route or a snap beyond 100 m.
- `nearest_seconds`: shortest verified walking time among checked candidates, even when
  beyond the selected walking limit; null if no walking time could be verified.
- `places`: reachable candidates, including recorded hours, OSM link and coordinates.

Retrieval and OSM timestamps accompany successful address results. Provider failure is
not represented as an empty category. Counts represent OSM objects and may include
duplicate representations of a venue; neither counts nor opening hours are exhaustive
or live. Pedestrian times include approximate endpoint access at 4.5 km/h.

`POST /api/walking-route` takes `origin` and `destination` points and returns pedestrian
geometry, duration, distance, directions and approximate endpoint-access links.
`POST /api/journey-map` takes ordered `legs` with mode, coordinates and optional trip/route,
plus `pipeline_run_id`. It returns GeoJSON with `official_transit`, `street_walk`,
`scheduled_stops`, `unverified_walk`, or `access` geometry classes. The last three are
rendered dashed; geometry does not change timetable or transfer-time calculations.
All three exploration endpoints require `X-Mobility-Action: explore` and limit bodies
to 64 KB. Journey maps accept at most 12 legs, with at most 200 input coordinates per leg.

## Universal audit fields

Analytical gold tables expose:

- `loaded_at`: source staging load timestamp or transformation timestamp.
- `pipeline_run_id`: ingestion run that introduced the source record.
- `source_snapshot_date`: source version from which the record derives.
- `dbt_invocation_id`: exact dbt execution that produced the model.
