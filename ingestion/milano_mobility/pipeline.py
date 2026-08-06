"""End-to-end ingestion pipeline used by the CLI and Airflow."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Iterator
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
def _download(
    url: str,
    target: Path,
    timeout: int,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> tuple[str | None, str | None]:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        total_header = response.headers.get("Content-Length")
        total = int(total_header) if total_header else None
        downloaded = 0
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                handle.write(chunk)
                downloaded += len(chunk)
                if on_progress:
                    on_progress(downloaded, total)
        return response.headers.get("ETag"), response.headers.get("Last-Modified")


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def _source_metadata(url: str, timeout: int) -> tuple[str | None, str | None]:
    """Read HTTP validators without transferring the feed body."""
    with requests.head(url, allow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        return response.headers.get("ETag"), response.headers.get("Last-Modified")


@contextmanager
def local_feed(
    source: str | Path,
    timeout: int,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> Iterator[tuple[Path, str | None, str | None]]:
    """Resolve an HTTP or local source to a temporary, seekable ZIP file."""
    source_text = str(source)
    with tempfile.TemporaryDirectory(prefix="milano-mobility-") as directory:
        target = Path(directory) / "feed.zip"
        if source_text.startswith(("http://", "https://")):
            etag, last_modified = _download(source_text, target, timeout, on_progress)
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
    progress_callback: Callable[[str, int, str], None] | None = None,
) -> PipelineResult:
    """Archive, validate, stage, and optionally publish one GTFS snapshot."""
    runtime = settings or Settings()
    run_id = pipeline_run_id or str(uuid.uuid4())
    database = Database(runtime.postgres_dsn)
    configure_logging()
    started = datetime.now(timezone.utc)

    def notify(stage: str, progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(stage, progress, message)

    def download_progress(downloaded: int, total: int | None) -> None:
        progress = 8 if not total else 5 + min(25, int(downloaded / total * 25))
        size = f"{downloaded / 1024 / 1024:.1f} MB"
        if total:
            size += f" of {total / 1024 / 1024:.1f} MB"
        notify("downloading", progress, f"Downloading official GTFS · {size}")

    notify("checking", 2, "Checking the official source for a newer snapshot")
    feed_text = str(feed)
    if feed_text.startswith(("http://", "https://")):
        try:
            etag, last_modified = _source_metadata(feed_text, runtime.request_timeout_seconds)
            existing_version = database.find_source_version(
                runtime.source_name,
                etag,
                None if etag else last_modified,
            )
            if existing_version:
                logger.info(
                    "source_version_already_downloaded",
                    pipeline_run_id=run_id,
                    duplicate_of=existing_version["pipeline_run_id"],
                    http_etag=etag,
                    http_last_modified=last_modified,
                )
                notify("unchanged", 100, "The latest snapshot is already downloaded")
                return PipelineResult(
                    pipeline_run_id=run_id,
                    status=ManifestStatus.SKIPPED,
                    sha256=str(existing_version["sha256"]),
                    object_uri=str(existing_version["object_uri"]),
                    row_counts={},
                    validation_status="skipped",
                )
        except requests.RequestException as error:
            logger.warning("source_metadata_unavailable", reason=str(error))

    object_store = ObjectStore(runtime)
    object_store.ensure_buckets(
        (runtime.raw_bucket, runtime.quarantine_bucket, runtime.curated_bucket)
    )
    notify("starting", 3, "Preparing the latest official snapshot")
    with local_feed(feed, runtime.request_timeout_seconds, download_progress) as (
        local_path,
        etag,
        last_modified,
    ):
        notify("verifying", 32, "Verifying the downloaded archive")
        digest = file_sha256(local_path)
        existing = database.find_duplicate(runtime.source_name, digest)
        if existing:
            logger.info(
                "feed_skipped",
                pipeline_run_id=run_id,
                duplicate_of=existing["pipeline_run_id"],
                sha256=digest,
            )
            notify("unchanged", 100, "The downloaded snapshot is already published")
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

        notify("validating", 40, "Validating the complete GTFS feed")
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

        notify("loading", 55, "Loading the validated snapshot into PostgreSQL")
        row_counts = database.load_staging(local_path, snapshot_date, run_id)
        database.update_status(run_id, ManifestStatus.VALIDATED, report.status)
        final_status = ManifestStatus.VALIDATED
        if build_warehouse:
            notify("modeling", 80, "Building and testing the analytical models")
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

        notify("published", 100, "The latest snapshot is ready")

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
