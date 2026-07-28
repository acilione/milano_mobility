FROM python:3.11-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ARG PIP_TRUSTED_HOST

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    DBT_PROJECT_DIR=/workspace/transformations/dbt

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --no-install-recommends -y libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock requirements-dbt.lock ./
RUN pip install --no-cache-dir -r requirements-dbt.lock

COPY pyproject.toml README.md ./
COPY ingestion ./ingestion
RUN pip install --no-cache-dir --no-deps .
COPY transformations ./transformations

ENTRYPOINT ["mobility"]
CMD ["--help"]
