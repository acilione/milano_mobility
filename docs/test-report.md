# Test report

## Baseline

- Project version: 1.0.0
- Validation date: 2026-07-28
- Python: 3.10.12
- dbt Core: 1.9.2
- dbt PostgreSQL adapter: 1.9.0

## Local evidence

| Check | Result |
|---|---|
| Python byte-code compilation | Passed |
| Ruff lint rules | Passed |
| Ruff formatting check | Passed |
| Strict mypy analysis | Passed, 11 package modules |
| Unit and contract tests | Passed, 32 tests |
| Measured unit coverage | 89.51% |
| Valid fixture CLI gate | Passed |
| Invalid foreign-key CLI gate | Passed, exit code 2 |
| dbt project parse | Passed |
| Docker Compose configuration parse | Passed |
| One-command Compose demo from empty volumes | Passed |
| Container image build | Passed |
| PostgreSQL staging and dbt publication | Passed |
| dbt models and data tests | Passed, 48 of 48 |
| Duplicate-payload idempotency | Passed, returned `SKIPPED` |
| Invalid foreign-key quarantine | Passed, returned `QUARANTINED` |
| Raw, curated, and quarantine object persistence | Passed |
| Read-only visual dashboard and JSON API | Passed |
| English repository-content scan | Passed |

The end-to-end run published 68 scheduled trips, 204 stop events, and six network-change
records. It was then repeated with the identical source payload and safely skipped. A
separate invalid feed was rejected without replacing the published marts, and both its
payload and quality report were persisted under the quarantine and curated prefixes.

The checked-in CI integration job repeats the same contract on an ephemeral Linux runner:
it starts PostgreSQL and MinIO, runs the versioned fixture through the pipeline and dbt
mart, asserts a non-empty Gold fact, repeats the payload to assert `SKIPPED`, and loads a
broken foreign-key fixture to assert `QUARANTINED`. It then starts the visual dashboard
through the read-only BI role and verifies that its API reports a ready analytical state.

## Fixture coverage

- `gtfs_v1.zip`: valid baseline including a post-midnight `24:10:00` departure.
- `gtfs_v2.zip`: modified stop, removed stop, added stop/route/trip.
- `gtfs_invalid_fk.zip`: a trip references a missing route and must be quarantined.

CI also runs a repository secret scan and builds the immutable application image on the
main branch.
