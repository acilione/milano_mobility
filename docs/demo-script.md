# Seven-minute portfolio demo

## 0:00–1:00 — Architecture and time semantics

Open the architecture diagram. Explain that `snapshot_date` is the acquired network
version while `service_date` is a day of operation. State clearly that the product
measures scheduled supply, not real-time punctuality.

## 1:00–2:00 — Start and ingest

Run `docker compose --profile demo run --build --rm demo`. Show the final `PUBLISHED`
result, then open the visual dashboard at <http://localhost:8501>. Use the KPI cards,
service trend, and stop constellation to establish the analytical product before showing
the immutable ZIP and manifest in the MinIO raw bucket.

## 2:00–3:00 — Quality and lineage

Open the JSON quality report and dbt documentation. Point out the blocking severity gate,
source freshness, model descriptions, and universal audit columns.

## 3:00–4:00 — Scheduled frequency

Run the first query from `dashboards/questions.sql`. Highlight service-hour values above
23 to demonstrate correct handling of post-midnight GTFS service.

## 4:00–5:15 — Network history

Run `make demo-second`. Query `marts.dim_stop` for `S2` to show its closed and current
type-2 versions. Query `marts.fact_network_change` to show added, removed, and modified
entities with old/new hashes.

## 5:15–6:15 — Dashboard and operations

Open the Metabase dashboard cards for frequency, coverage, changes, and daily context.
Show the Airflow quality DAG and the operations runbook for backfill and restore.

## 6:15–7:00 — Architecture decision and next steps

Close with ADR-001: PostgreSQL and S3-compatible storage make the demo laptop-friendly
while preserving a direct managed-cloud migration path. Mention GTFS-Realtime,
incremental fact partitions, and managed secret/identity services as deliberate next
steps.
