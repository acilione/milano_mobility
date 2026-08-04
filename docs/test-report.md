# Test report

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
| Unit and contract tests | Passed, 33 tests |
| Measured unit coverage | 80.61% |
| Valid fixture CLI gate | Passed |
| Invalid foreign-key CLI gate | Passed, exit code 2 |
| dbt project parse | Passed |
| Docker Compose configuration parse | Passed |
| One-command Compose demo from empty volumes | Passed |
| Container image build | Passed |
| PostgreSQL staging and dbt publication | Passed |
| Full official GTFS streaming validation | Passed, 7,058,600 stop times |
| dbt models and data tests | Passed, 51 of 51 in 29.83 seconds |
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
