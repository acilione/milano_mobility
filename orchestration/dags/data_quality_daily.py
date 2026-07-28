"""Post-publication dbt test DAG."""

from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

with DAG(
    dag_id="data_quality_daily",
    schedule="0 8 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    dagrun_timeout=timedelta(minutes=10),
    tags=["quality", "dbt"],
) as dag:
    BashOperator(
        task_id="dbt_test_gold",
        bash_command=(
            "dbt test --project-dir /workspace/transformations/dbt "
            "--profiles-dir /workspace/transformations/dbt --select tag:gold"
        ),
    )
