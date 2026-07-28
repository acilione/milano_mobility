"""Manual, idempotent historical reconstruction DAG."""

from datetime import datetime, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="historical_backfill",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    tags=["backfill", "manual"],
    params={"snapshot_date": "2026-07-28", "raw_feed_path": ""},
) as dag:
    BashOperator(
        task_id="rebuild_snapshot",
        bash_command=(
            "mobility run --feed '{{ params.raw_feed_path }}' "
            "--snapshot-date '{{ params.snapshot_date }}' --build"
        ),
    )
