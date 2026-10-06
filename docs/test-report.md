# Test report

## Compact workspaces across planning tabs — 2026-10-06

- TypeScript checking, the frontend build and `git diff --check` passed.
- Chromium verified the journey wizard, three included addresses, a collapsed address
  list, an expanded editor and the seven-row comparison. Each desktop overview fits
  at 1366 × 768. Results remain alongside controls; daily journeys and location maps
  open in native dialogs with Escape support and route-map cleanup.
- A live Via Padova-to-Duomo comparison produced scheduled morning/return journeys and
  displayed official transit geometry plus pedestrian geometry in its route window.
  Saving, sharing, CSV export, cancelling map placement and returning invalid inputs
  to a visible address editor passed.
- Commute-area checks covered the map/list workspace, reachable-stop filtering, journey
  windows, route maps, explicit destination placement and method information.
- Network checks covered all four views, stop search, connected-route filtering,
  selection reset and keyboard access to calendar/hourly-chart values.
- Journey, area and network overviews fit at 390 × 844 with mobile settings collapsed.
  The network calendar, hourly departures and changes views also fit at 1366 × 768.
  No page errors or horizontal document overflow occurred. Longer lists, expanded
  details and small/zoomed viewports retain scrolling where needed.

## Category classification audit — 2026-10-06

- 206 unit tests passed with 85.61% overall coverage and 100% statement coverage of
  the classification module. Cases span all twelve categories: explicit positive
  service tags, related but different types, misleading names/brands, ambiguous
  primary types, multiple independently mapped services, lifecycle conflicts,
  restricted access, category provenance and missing names.
- TypeScript, the frontend build, Ruff, formatting and strict mypy (17 modules) passed.
  A wheel built in an isolated temporary directory includes the shared JSON catalog
  and classification module. The frontend Docker stage now copies that catalog too.
- A live browser comparison around Piazza del Duomo successfully queried all twelve
  categories with the new generated Overpass filters and pedestrian routing. Results
  retained their supporting tags; place details displayed the recorded subtype and
  category definition. The category reference displayed all twelve definitions.
- Browser regression checks passed for three addresses, all categories, selection
  toggles, route dialogs, autocomplete, provider failures and mobile controls. The
  overview still fits at 1366 × 768 and 390 × 844 (mobile filters collapsed), with
  no page errors or horizontal document overflow.

These checks validate classification rules against recorded tags, not the independent
truth of every source record. Strict rules can exclude incompletely tagged services.

## Compact nearby comparison and book categories — 2026-10-06

- 139 unit tests passed. The added discovery-to-comparison test separates bookshops
  (`shop=books`) from libraries (`amenity=library`), excludes private libraries and
  stationery shops, and verifies the walking limit independently for both categories.
- TypeScript, the production frontend build, Ruff, formatting and strict mypy passed.
- Chromium tested three addresses and all twelve categories using controlled responses.
  At 1366 × 768 the controls, table and source notes fit without document scrolling.
  At 390 × 844 the entire overview fits with the mobile filters collapsed. Smaller
  viewports and enlarged text retain normal scrolling; place details scroll separately.
- Browser checks covered immediate address/category toggles, incomplete-data markers,
  shortest-walk highlights, modal routes and directions, Escape/close cleanup, source
  details, mobile filters, unavailable/failed providers, keyboard autocomplete and
  address addition/removal. No page errors or horizontal document overflow occurred.
- Live Overpass and pedestrian routing returned 17 mapped bookshops near Porta Genova,
  with 13 reachable within 15 minutes. A live browser comparison around Piazza del
  Duomo returned both categories, including 12 mapped libraries and 11 reachable
  libraries. These are provider results at test time, not completeness guarantees.

## Address amenities and inline routes — 2026-10-06

- 138 unit tests passed with 85.09% measured coverage; nearby discovery/comparison reached
  97%, route-map assembly 94%, and the underlying connection scan remained at 100%.
  Cases include private/malformed/duplicate OSM entries, bounded candidate counts,
  unknown routes, unavailable addresses, API bounds, snapping limits, official geometry,
  fallback styling metadata, and forward/return coordinate order.
- Ruff, formatting, strict mypy (16 modules), TypeScript checking and the frontend build
  passed. The new `commute_trip_shapes` dbt model built 175,790 trip mappings in the
  full-data database. Existing routing models were reused.
- A live Overpass/OSRM comparison covered Via Padova 10 and Porta Genova across cafés,
  supermarkets, cinemas, pharmacies, parks and post offices. Sampled categories were
  labelled as shortlists; walking routes and directions were retrieved successfully.
- Chromium exercised address/category inclusion toggles, shared address state, a real
  scheduled journey with both official transit geometry and pedestrian paths, inline
  maps in commute-area journey details, input invalidation and a 390 px mobile layout.
  No page errors or horizontal document overflow were observed.

These checks do not establish completeness of OSM amenities, current opening hours,
accessibility or observed travel times. Nearby reachability is pedestrian only and
checks a maximum of 20 candidates per category/address within the chosen radius.

## Guided planning workflow — 2026-10-06

- TypeScript checking and the production frontend build passed.
- Chromium completed the destination → apartments → travel days flow against the
  published timetable, including autocomplete, field validation and focus, preserved
  settings across all three tools, explicit map-placement mode, result navigation,
  daily journey expansion, browser saving and CSV download.
- A missing apartment after editing returns the user to the correct field. Clicking
  the map outside placement mode leaves selected addresses unchanged.
- Desktop (1440 px) and mobile (390 px) checks found no page errors or horizontal
  document overflow. The interface now explains tool purpose, setup steps and result
  interpretation without changing the scheduled routing calculation.

## Milan address autocomplete — 2026-10-06

- All 118 unit tests passed (84.04% measured coverage), including municipality filtering,
  duplicate removal and the five-suggestion limit. Ruff, mypy, TypeScript checking and
  the frontend production build passed.
- Chromium verified live partial-street lookup (`Via Pad`), the three-character minimum,
  600 ms debounce, arrow/Enter selection, mouse selection, focus restoration, Escape/Tab
  dismissal, stale response cancellation, text composition, empty/error/retry states,
  added/removed apartment fields and a 390 px mobile layout without horizontal overflow.
- Address suggestions use Photon with a Milan bounding box and municipality filtering.
  Street results can represent a street segment; include a house number for a more
  precise apartment location. Provider requests remain cached and rate limited.

## Apartment comparison validation — 2026-10-06

- Ruff lint/formatting, strict mypy (14 modules), TypeScript checking and the frontend
  production build passed. All 117 unit tests passed with 83.94% measured coverage;
  comparison routing reached 98% and the underlying commute scan remained at 100%.
- Comparison cases cover outbound/return direction, separate later departures, walking
  limits, missing pedestrian routes, incomplete weeks, input validation, provider failures,
  address caching and immutable local archives.
- Downloaded and validated the full official Milan feed: 4,897 stops, 166 routes,
  175,790 trips and 4,116,522 stop times. All 63 dbt build/test nodes passed in a fresh
  PostgreSQL 17 database. The published service window was September 14–October 19.
- The live read-only commute integration check passed against that publication. In a
  separate fixture database, filesystem-backed ingestion, publication, duplicate skipping
  and invalid-feed quarantine passed both pipeline integration tests.
- Chromium exercised two apartments, actual morning/return journeys, daily details,
  CSV downloads, stale-result invalidation, unavailable dates, live Photon address search,
  local saving, shared-link restoration and recovery after a simulated HTTP 503.
  Desktop and 390 px mobile layouts had no page errors or horizontal document overflow.
- The original MinIO image could not be downloaded in this environment. This run used
  the optional persistent filesystem archive; a new full Docker image and the S3-backed
  integration path were not verified. The live-timetable integration test is now opt-in
  so a fresh fixture-only CI database does not require a preloaded Milan publication.

These checks verify scheduled calculations and provider integration, not observed travel
times or pedestrian accessibility. Transfer walks remain estimates. Automatic updates
are configured at a 24-hour interval; the full elapsed interval was not exercised.

## Commute map validation — 2026-09-07

- Ruff lint and formatting, strict mypy (12 modules), TypeScript checking and production
  image build passed.
- All 74 unit tests passed; total coverage was 82.94%, including 100% of the routing module.
  Cases cover through-riding, transfer margins and walking transfers, boarding/alighting
  restrictions, the three-boarding limit, deadlines, negative after-midnight offsets and
  invalid API parameters.
- The three commute models and the connection-integrity test passed on the existing full
  warehouse. The connection table contains 9,326,313 rows across stored snapshots. The
  revised build took 119 seconds; the complete selected build/test took 338 seconds.
- A read-only integration test against the published Milan timetable passed. A request
  for Duomo on 2026-09-07, arriving by 09:00 within 30 minutes with 10-minute walks, scanned
  16,547 connections and returned 1,175 reachable stops. An observed request took about
  six seconds while other validation was active; this is not a latency guarantee.
- Chromium checks exercised actual API results, journey details, 30/45-minute budgets,
  saved browser preferences, weekend/evening changes, mobile overflow at 390 px and
  recovery after a simulated HTTP 503. No JavaScript page errors were observed.

These checks validate the implementation against schedules. They do not validate walking
paths against streets or establish real-world punctuality. Shading uses conservative
100 m cells within estimated walking catchments.

## Baseline

- Project version: 1.0.0
- Validation date: 2026-07-31
- Python: 3.11.15 (application container)
- dbt Core: 1.9.2
- dbt PostgreSQL adapter: 1.9.0

## Local evidence

| Check | Result |
|---|---|
| Python byte-code compilation | Passed |
| Ruff lint rules | Passed |
| Ruff formatting check | Passed |
| Strict mypy analysis | Passed, 11 package modules |
| Unit and contract tests | Passed, 35 tests |
| Measured unit coverage | 80.84% |
| Valid fixture CLI gate | Passed |
| Invalid foreign-key CLI gate | Passed, exit code 2 |
| dbt project parse | Passed |
| Docker Compose configuration parse | Passed |
| One-command Compose demo from empty volumes | Passed |
| Container image build | Passed |
| PostgreSQL staging and dbt publication | Passed |
| Full official GTFS streaming validation | Passed, 7,058,600 stop times |
| dbt models and data tests | Passed, 49 of 49 in 35.84 seconds |
| Duplicate-payload idempotency | Passed, returned `SKIPPED` |
| Invalid foreign-key quarantine | Passed, returned `QUARANTINED` |
| Raw, curated, and quarantine object persistence | Passed |
| Read-only visual dashboard and JSON API | Passed |
| English repository-content scan | Passed |

The clean end-to-end run downloaded the live official Comune di Milano/AMAT feed and
archived SHA-256
`456cbebaba38a170666d99051ede27b28eed68bcb4e18c2c5a12e410652f00c9` (55,364,907
bytes). It staged 1 agency, 4,909 stops, 166 routes, 299,322 trips, 7,058,600 stop times,
and 4,724 calendar exceptions. Route types comprise 5 metro, 17 tram, and 144 bus or
trolleybus routes.

The published marts expose 856,195 scheduled trips and 20,050,699 scheduled stop events
over 50 service days, while the dashboard API reports all 166 routes and 4,909 stops.
The complete service-day facts remain views and the dashboard tables are compact
aggregates, preventing local copies of tens of millions of repeated rows. Replaying an
identical payload safely returns `SKIPPED`. A separate invalid feed is rejected without
replacing published marts, and its payload and quality report are persisted under the
quarantine and curated prefixes.

The checked-in CI integration job repeats the same contract on an ephemeral Linux runner:
it starts PostgreSQL and MinIO, runs the versioned fixture through the pipeline and dbt
mart, asserts a non-empty Gold fact, repeats the payload to assert `SKIPPED`, and loads a
broken foreign-key fixture to assert `QUARANTINED`. It then starts the visual dashboard
through the read-only BI role and verifies that its API reports a ready analytical state.

## Test fixture coverage

- `gtfs_v1.zip`: seven-route test baseline with 15 stops, 28 trip patterns, and
  post-midnight departures.
- `gtfs_v2.zip`: eight-route test comparison with a modified stop, a replacement stop,
  and a new tram corridor.
- `gtfs_invalid_fk.zip`: a trip references a missing route and must be quarantined.

These small synthetic ZIPs are used only for fast deterministic CI contracts. The
one-command user demo always downloads the full official dataset.

CI also runs a repository secret scan and builds the immutable application image on the
main branch.
