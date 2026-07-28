"""End-to-end ingestion pipeline used by the CLI and Airflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import requests
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from milano_mobility.config import Settings
from milano_mobility.database import Database
from milano_mobility.gtfs import file_sha256
from milano_mobility.models import Manifest, ManifestStatus, PipelineResult
from milano_mobility.storage import ObjectStore
from milano_mobility.validation import validate_feed

logger = structlog.get_logger()


class ValidationGateError(RuntimeError):
    """Raised when a feed is quarantined by a blocking quality rule."""


def configure_logging() -> None:
    """Configure JSON logs suitable for local viewing and aggregation."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def _download(url: str, target: Path, timeout: int) -> tuple[str | None, str | None]:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
        return response.headers.get("ETag"), response.headers.get("Last-Modified")


@contextmanager
def local_feed(source: str | Path, timeout: int) -> Iterator[tuple[Path, str | None, str | None]]:
    """Resolve an HTTP or local source to a temporary, seekable ZIP file."""
    source_text = str(source)
    with tempfile.TemporaryDirectory(prefix="milano-mobility-") as directory:
        target = Path(directory) / "feed.zip"
        if source_text.startswith(("http://", "https://")):
            etag, last_modified = _download(source_text, target, timeout)
        else:
            source_path = Path(source).expanduser().resolve()
            if not source_path.is_file():
                raise FileNotFoundError(f"GTFS source does not exist: {source_path}")
            shutil.copyfile(source_path, target)
            etag = None
            last_modified = None
        yield target, etag, last_modified


def run_pipeline(
    feed: str | Path,
    snapshot_date: date,
    *,
    settings: Settings | None = None,
    pipeline_run_id: str | None = None,
    build_warehouse: bool = False,
) -> PipelineResult:
    """Archive, validate, stage, and optionally publish one GTFS snapshot."""
    runtime = settings or Settings()
    run_id = pipeline_run_id or str(uuid.uuid4())
    database = Database(runtime.postgres_dsn)
    object_store = ObjectStore(runtime)
    object_store.ensure_buckets(
        (runtime.raw_bucket, runtime.quarantine_bucket, runtime.curated_bucket)
    )
    configure_logging()
    started = datetime.now(timezone.utc)

    with local_feed(feed, runtime.request_timeout_seconds) as (
        local_path,
        etag,
        last_modified,
    ):
        digest = file_sha256(local_path)
        existing = database.find_duplicate(runtime.source_name, digest)
        if existing:
            logger.info(
                "feed_skipped",
                pipeline_run_id=run_id,
                duplicate_of=existing["pipeline_run_id"],
                sha256=digest,
            )
            return PipelineResult(
                pipeline_run_id=run_id,
                status=ManifestStatus.SKIPPED,
                sha256=digest,
                object_uri=str(existing["object_uri"]),
                row_counts={},
                validation_status="skipped",
            )

        prefix = f"gtfs/snapshot_date={snapshot_date.isoformat()}/sha256={digest}"
        object_key = f"{prefix}/feed.zip"
        object_uri = object_store.put_file(
            runtime.raw_bucket,
            object_key,
            local_path,
            {
                "sha256": digest,
                "snapshot-date": snapshot_date.isoformat(),
                "pipeline-run-id": run_id,
            },
        )
        manifest = Manifest(
            source=runtime.source_name,
            retrieved_at=started,
            effective_snapshot_date=snapshot_date,
            object_uri=object_uri,
            sha256=digest,
            http_etag=etag,
            http_last_modified=last_modified,
            bytes=local_path.stat().st_size,
            pipeline_run_id=run_id,
            status=ManifestStatus.RECEIVED,
        )
        database.register_manifest(manifest)
        object_store.put_json(runtime.raw_bucket, f"{prefix}/manifest.json", manifest.to_dict())

        report = validate_feed(
            local_path,
            run_id,
            snapshot_date,
            runtime.invalid_coordinate_threshold,
        )
        report_key = f"quality/snapshot_date={snapshot_date.isoformat()}/{run_id}.json"
        object_store.put_json(runtime.curated_bucket, report_key, report.to_dict())
        if report.blocking:
            quarantine_key = f"{prefix}/feed.zip"
            object_store.copy(
                runtime.raw_bucket,
                object_key,
                runtime.quarantine_bucket,
                quarantine_key,
            )
            database.update_status(
                run_id,
                ManifestStatus.QUARANTINED,
                report.status,
                json.dumps(report.to_dict()),
            )
            logger.error(
                "feed_quarantined",
                pipeline_run_id=run_id,
                issue_count=len(report.issues),
                sha256=digest,
            )
            raise ValidationGateError(
                f"Snapshot {snapshot_date} failed validation; report: "
                f"s3://{runtime.curated_bucket}/{report_key}"
            )

        row_counts = database.load_staging(local_path, snapshot_date, run_id)
        database.update_status(run_id, ManifestStatus.VALIDATED, report.status)
        final_status = ManifestStatus.VALIDATED
        if build_warehouse:
            try:
                _run_dbt()
            except subprocess.CalledProcessError as error:
                database.update_status(
                    run_id,
                    ManifestStatus.FAILED,
                    report.status,
                    f"dbt build failed with exit code {error.returncode}",
                )
                raise
            database.update_status(run_id, ManifestStatus.PUBLISHED, report.status)
            final_status = ManifestStatus.PUBLISHED

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        logger.info(
            "pipeline_completed",
            pipeline_run_id=run_id,
            status=final_status.value,
            duration_seconds=duration,
            row_counts=row_counts,
            sha256=digest,
        )
        return PipelineResult(
            pipeline_run_id=run_id,
            status=final_status,
            sha256=digest,
            object_uri=object_uri,
            row_counts=row_counts,
            validation_status=report.status,
        )


def _run_dbt() -> None:
    source_checkout = Path(__file__).resolve().parents[2]
    project_dir = Path(
        os.getenv("DBT_PROJECT_DIR", str(source_checkout / "transformations" / "dbt"))
    )
    subprocess.run(
        [
            "dbt",
            "build",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir),
        ],
        check=True,
    )
