# Operations runbook

## Triage a failed ingestion

1. Open the Airflow task log and record `pipeline_run_id`, snapshot, and SHA-256.
2. Read `s3://curated/quality/snapshot_date=<date>/<run-id>.json`.
3. If status is `QUARANTINED`, fix the source contract or approve a corrected source; do
   not edit staged data to bypass a blocking rule.
4. Confirm the previous published mart remains queryable.
5. Re-run with the corrected ZIP. A byte-identical valid feed becomes `SKIPPED`.

## Backfill from raw

1. Identify the archived key and verify its object SHA-256 against the manifest.
2. Trigger `historical_backfill` with `snapshot_date` and a mounted/downloaded raw path.
3. Run `dbt build`; do not use `--full-refresh` unless an administrator has approved it.
4. Compare row counts and KPI results with the quality report.

Raw is the reconstruction source, so an archived snapshot must never be downloaded again
from the publisher for a routine backfill.

## Republish a snapshot

Run `dbt build` using the existing staging partitions, verify all Gold tests, then update
the manifest only through the pipeline operator. Record the reason and dbt invocation in
the incident/change ticket.

## Invalidate an erroneous version

Do not delete raw data. Mark the manifest as failed through an approved migration, rebuild
models so only accepted states are selected, and attach the validation evidence. Confirm
that the previous valid mart is restored.

## Backup and restore

- Back up PostgreSQL daily; retain at least seven daily copies for the demo.
- Enable object versioning and lifecycle protection on raw storage.
- Monthly, restore both into an isolated Compose project and run `dbt build`.
- Demonstration targets: metadata/warehouse RPO 24 hours, RTO 4 hours.

## Alerts

- Pipeline duration warning at 30 minutes; critical at 60 minutes.
- Valid-source freshness warning after 48 hours.
- Row-count warning beyond 30% from the moving median.
- Any critical/high validation issue pages the operator.
- Gold publish timestamp beyond SLA is critical.
