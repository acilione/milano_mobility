# Test report

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
