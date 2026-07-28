#!/usr/bin/env bash
set -euo pipefail

psql --set ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ingestion_user="$INGESTION_DB_USER" \
  --set ingestion_password="$INGESTION_DB_PASSWORD" \
  --set transformer_user="$TRANSFORMER_DB_USER" \
  --set transformer_password="$TRANSFORMER_DB_PASSWORD" \
  --set bi_user="$BI_DB_USER" \
  --set bi_password="$BI_DB_PASSWORD" \
  --file /opt/mobility-init/schema.sql

psql --set ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname postgres \
  --set airflow_user="$AIRFLOW_DB_USER" \
  --set airflow_password="$AIRFLOW_DB_PASSWORD" \
  --set metabase_user="$METABASE_DB_USER" \
  --set metabase_password="$METABASE_DB_PASSWORD" \
  --file /opt/mobility-init/platform-databases.sql
