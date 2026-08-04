FROM node:22.14.0-bookworm-slim@sha256:1c18d9ab3af4585870b92e4dbc5cac5a0dc77dd13df1a5905cea89fc720eb05b AS frontend-builder

WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json frontend/tsconfig.json ./
RUN npm ci
COPY frontend/src ./src
COPY frontend/scripts ./scripts
RUN npm run typecheck && npm run build

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
COPY --from=frontend-builder /frontend/dist ./ingestion/milano_mobility/static/dist
RUN pip install --no-cache-dir --no-deps .
COPY transformations ./transformations

ENTRYPOINT ["mobility"]
CMD ["--help"]
