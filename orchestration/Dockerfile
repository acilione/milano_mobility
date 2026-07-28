FROM apache/airflow:2.10.5-python3.11

ARG PIP_TRUSTED_HOST
ENV PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

COPY requirements.lock requirements-dbt.lock /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-dbt.lock

COPY pyproject.toml README.md /opt/airflow/project/
COPY ingestion /opt/airflow/project/ingestion
RUN pip install --no-cache-dir --no-deps /opt/airflow/project
