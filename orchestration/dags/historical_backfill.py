"""Manual, idempotent historical reconstruction DAG."""

import os
from datetime import datetime, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

DAG_START_DATE = datetime.fromisoformat(os.getenv("AIRFLOW_DAG_START_DATE", "2024-01-01")).replace(
    tzinfo=timezone.utc
)

with DAG(
    dag_id="historical_backfill",
    schedule=None,
    start_date=DAG_START_DATE,
    catchup=False,
    tags=["backfill", "manual"],
    params={"snapshot_date": "", "raw_feed_path": ""},
) as dag:
    BashOperator(
        task_id="rebuild_snapshot",
        bash_command=(
            "mobility run --feed '{{ params.raw_feed_path }}' "
            "{% if params.snapshot_date %}"
            "--snapshot-date '{{ params.snapshot_date }}' "
            "{% endif %}--build"
        ),
    )
