"""Daily weather enrichment and operator-controlled range backfill."""

from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="weather_backfill",
    schedule="0 7 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
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
