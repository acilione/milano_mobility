"""Environment-backed application settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import quote_plus


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    """Runtime settings shared by the CLI and Airflow tasks."""

    postgres_host: str = field(default_factory=lambda: _env("POSTGRES_HOST", "postgres"))
    postgres_port: int = field(default_factory=lambda: int(_env("POSTGRES_PORT", "5432")))
    postgres_db: str = field(default_factory=lambda: _env("POSTGRES_DB", "mobility"))
    postgres_user: str = field(
        default_factory=lambda: _env("INGESTION_DB_USER", "ingestion_writer")
    )
    postgres_password: str = field(
        default_factory=lambda: _env("INGESTION_DB_PASSWORD", "ingestion_dev")
    )

    s3_endpoint_url: str = field(
        default_factory=lambda: _env("S3_ENDPOINT_URL", "http://minio:9000")
    )
    s3_access_key: str = field(default_factory=lambda: _env("S3_ACCESS_KEY", "minio"))
    s3_secret_key: str = field(default_factory=lambda: _env("S3_SECRET_KEY", "minio_dev_password"))
    s3_region: str = field(default_factory=lambda: _env("S3_REGION", "eu-south-1"))
    raw_bucket: str = field(default_factory=lambda: _env("S3_RAW_BUCKET", "raw"))
    quarantine_bucket: str = field(
        default_factory=lambda: _env("S3_QUARANTINE_BUCKET", "quarantine")
    )
    curated_bucket: str = field(default_factory=lambda: _env("S3_CURATED_BUCKET", "curated"))

    source_name: str = field(default_factory=lambda: _env("GTFS_SOURCE_NAME", "gtfs"))
    source_url: str = field(default_factory=lambda: _env("GTFS_SOURCE_URL", ""))
    service_area_id: str = field(default_factory=lambda: _env("SERVICE_AREA_ID", "service-area"))
    service_area_latitude: float = field(
        default_factory=lambda: float(_env("SERVICE_AREA_LATITUDE", "0"))
    )
    service_area_longitude: float = field(
        default_factory=lambda: float(_env("SERVICE_AREA_LONGITUDE", "0"))
    )
    service_timezone: str = field(default_factory=lambda: _env("SERVICE_TIMEZONE", "UTC"))
    weather_api_url: str = field(default_factory=lambda: _env("WEATHER_API_URL", ""))
    invalid_coordinate_threshold: float = field(
        default_factory=lambda: float(_env("GTFS_INVALID_COORDINATE_THRESHOLD", "0.005"))
    )
    request_timeout_seconds: int = field(
        default_factory=lambda: int(_env("GTFS_REQUEST_TIMEOUT_SECONDS", "60"))
    )

    @property
    def postgres_dsn(self) -> str:
        """Return a libpq connection string."""
        return (
            f"host={self.postgres_host} port={self.postgres_port} "
            f"dbname={self.postgres_db} user={self.postgres_user} "
            f"password={self.postgres_password}"
        )

    @property
    def dbt_database_url(self) -> str:
        """Return a URL useful for diagnostic output without exposing it in logs."""
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql://{user}:{password}@{self.postgres_host}:"
            f"{self.postgres_port}/{self.postgres_db}"
        )
