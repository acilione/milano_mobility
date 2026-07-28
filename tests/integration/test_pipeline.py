from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path

import pytest

from milano_mobility.config import Settings

FIXTURES = Path(__file__).parents[1] / "fixtures"


def integration_settings() -> Settings:
    return Settings(
        postgres_host=os.getenv("TEST_POSTGRES_HOST", "localhost"),
        s3_endpoint_url=os.getenv("TEST_S3_ENDPOINT_URL", "http://localhost:9000"),
    )


@pytest.mark.integration
def test_valid_feed_is_idempotent_and_published() -> None:
    import psycopg

    from milano_mobility.models import ManifestStatus
    from milano_mobility.pipeline import run_pipeline

    settings = integration_settings()
    result = run_pipeline(
        FIXTURES / "gtfs_v1.zip",
        date(2026, 7, 28),
        settings=settings,
        pipeline_run_id=f"integration-{uuid.uuid4()}",
        build_warehouse=True,
    )
    duplicate = run_pipeline(
        FIXTURES / "gtfs_v1.zip",
        date(2026, 7, 28),
        settings=settings,
        pipeline_run_id=f"integration-{uuid.uuid4()}",
        build_warehouse=False,
    )
    assert result.status is ManifestStatus.PUBLISHED
    assert duplicate.status is ManifestStatus.SKIPPED
    with psycopg.connect(settings.postgres_dsn) as connection:
        mart_count = connection.execute("select count(*) from marts.fact_stop_event").fetchone()
    assert mart_count is not None
    assert mart_count[0] > 0


@pytest.mark.integration
def test_invalid_feed_is_quarantined_without_replacing_marts() -> None:
    import psycopg

    from milano_mobility.pipeline import ValidationGateError, run_pipeline

    settings = integration_settings()
    run_id = f"integration-{uuid.uuid4()}"
    with pytest.raises(ValidationGateError):
        run_pipeline(
            FIXTURES / "gtfs_invalid_fk.zip",
            date(2026, 7, 29),
            settings=settings,
            pipeline_run_id=run_id,
        )
    with psycopg.connect(settings.postgres_dsn) as connection:
        status = connection.execute(
            "select status from audit.ingestion_manifest where pipeline_run_id = %s",
            (run_id,),
        ).fetchone()
    assert status == ("QUARANTINED",)
