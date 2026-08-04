"""Post-publication dbt test DAG."""

import os
from datetime import datetime, timedelta, timezone

from airflow import DAG
from airflow.operators.bash import BashOperator

DAG_START_DATE = datetime.fromisoformat(os.getenv("AIRFLOW_DAG_START_DATE", "2024-01-01")).replace(
    tzinfo=timezone.utc
)

with DAG(
    dag_id="data_quality_daily",
    schedule=os.getenv("DATA_QUALITY_SCHEDULE", "0 8 * * *"),
    start_date=DAG_START_DATE,
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
