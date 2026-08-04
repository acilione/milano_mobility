"""Daily weather enrichment and operator-controlled range backfill."""

import os
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

DAG_START_DATE = datetime.fromisoformat(os.getenv("AIRFLOW_DAG_START_DATE", "2024-01-01")).replace(
    tzinfo=timezone.utc
)

with DAG(
    dag_id="weather_backfill",
    schedule=os.getenv("WEATHER_BACKFILL_SCHEDULE", "0 7 * * *"),
    start_date=DAG_START_DATE,
    catchup=False,
    dagrun_timeout=timedelta(minutes=20),
    tags=["weather", "enrichment", "backfill"],
    params={"date_from": "", "date_to": ""},
) as dag:
    BashOperator(
        task_id="fetch_archive_and_merge",
        bash_command=(
            "mobility weather-backfill "
            "--date-from '{{ params.date_from or ds }}' "
            "--date-to '{{ params.date_to or ds }}'"
        ),
        retries=3,
        retry_delay=timedelta(minutes=2),
        retry_exponential_backoff=True,
    )
