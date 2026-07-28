"""Daily GTFS ingestion, validation, transformation, and publication DAG."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException, AirflowSkipException
from airflow.operators.empty import EmptyOperator
from airflow.utils.context import get_current_context

from milano_mobility.config import Settings
from milano_mobility.database import Database
from milano_mobility.gtfs import file_sha256
from milano_mobility.models import Manifest, ManifestStatus
from milano_mobility.pipeline import local_feed
from milano_mobility.storage import ObjectStore
from milano_mobility.validation import validate_feed

DEFAULT_ARGS = {
    "owner": "data-engineering",
    "depends_on_past": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=2),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=15),
}


@dag(
    dag_id="gtfs_ingestion",
    schedule="0 6 * * *",
    start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    dagrun_timeout=timedelta(minutes=60),
    tags=["gtfs", "batch", "gold"],
    params={
        "snapshot_date": "",
        "feed_url": "",
        "force_download": False,
    },
)
def gtfs_ingestion() -> None:
    """Build the task graph while keeping every state transition explicit."""

    @task
    def check_source() -> dict[str, str]:
        context = get_current_context()
        settings = Settings()
        feed_url = str(context["params"].get("feed_url") or settings.source_url)
        if not feed_url:
            raise AirflowFailException("Set GTFS_SOURCE_URL or supply the feed_url DAG parameter.")
        force_download = bool(context["params"].get("force_download", False))
        if feed_url.startswith(("http://", "https://")) and not force_download:
            response = requests.head(
                feed_url,
                timeout=settings.request_timeout_seconds,
                allow_redirects=True,
            )
            if response.status_code not in {405, 501}:
                response.raise_for_status()
                existing = Database(settings.postgres_dsn).find_source_version(
                    settings.source_name,
                    response.headers.get("ETag"),
                    response.headers.get("Last-Modified"),
                )
                if existing:
                    raise AirflowSkipException(
                        f"HTTP source version already processed by {existing['pipeline_run_id']}."
                    )
        requested_date = str(context["params"].get("snapshot_date") or "")
        snapshot_date = requested_date or context["logical_date"].date().isoformat()
        return {
            "feed_url": feed_url,
            "snapshot_date": snapshot_date,
            "pipeline_run_id": str(uuid.uuid4()),
        }

    @task
    def download_and_hash(metadata: dict[str, str]) -> dict[str, str]:
        settings = Settings()
        target_directory = Path("/opt/airflow/data") / metadata["pipeline_run_id"]
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / "feed.zip"
        with local_feed(metadata["feed_url"], settings.request_timeout_seconds) as (
            source,
            etag,
            last_modified,
        ):
            shutil.copyfile(source, target)
        return {
            **metadata,
            "local_path": str(target),
            "sha256": file_sha256(target),
            "http_etag": etag or "",
            "http_last_modified": last_modified or "",
        }

    @task
    def register_manifest(metadata: dict[str, str]) -> dict[str, str]:
        settings = Settings()
        database = Database(settings.postgres_dsn)
        duplicate = database.find_duplicate(settings.source_name, metadata["sha256"])
        if duplicate:
            raise AirflowSkipException(
                f"Payload already processed by {duplicate['pipeline_run_id']}."
            )
        store = ObjectStore(settings)
        store.ensure_buckets(
            (settings.raw_bucket, settings.quarantine_bucket, settings.curated_bucket)
        )
        snapshot_date = date.fromisoformat(metadata["snapshot_date"])
        prefix = f"gtfs/snapshot_date={snapshot_date.isoformat()}/sha256={metadata['sha256']}"
        key = f"{prefix}/feed.zip"
        object_uri = store.put_file(
            settings.raw_bucket,
            key,
            Path(metadata["local_path"]),
            {
                "sha256": metadata["sha256"],
                "snapshot-date": metadata["snapshot_date"],
                "pipeline-run-id": metadata["pipeline_run_id"],
            },
        )
        manifest = Manifest(
            source=settings.source_name,
            retrieved_at=datetime.now(timezone.utc),
            effective_snapshot_date=snapshot_date,
            object_uri=object_uri,
            sha256=metadata["sha256"],
            bytes=Path(metadata["local_path"]).stat().st_size,
            pipeline_run_id=metadata["pipeline_run_id"],
            status=ManifestStatus.RECEIVED,
            http_etag=metadata["http_etag"] or None,
            http_last_modified=metadata["http_last_modified"] or None,
        )
        database.register_manifest(manifest)
        store.put_json(settings.raw_bucket, f"{prefix}/manifest.json", manifest.to_dict())
        return {**metadata, "raw_key": key, "object_uri": object_uri}

    @task
    def extract_to_staging(metadata: dict[str, str]) -> dict[str, Any]:
        settings = Settings()
        database = Database(settings.postgres_dsn)
        row_counts = database.load_staging(
            Path(metadata["local_path"]),
            date.fromisoformat(metadata["snapshot_date"]),
            metadata["pipeline_run_id"],
        )
        database.update_status(metadata["pipeline_run_id"], ManifestStatus.VALIDATED, "passed")
        return {**metadata, "row_counts": row_counts}

    @task(retries=0)
    def validate_gtfs(metadata: dict[str, Any]) -> dict[str, Any]:
        settings = Settings()
        report = validate_feed(
            Path(metadata["local_path"]),
            metadata["pipeline_run_id"],
            date.fromisoformat(metadata["snapshot_date"]),
            settings.invalid_coordinate_threshold,
        )
        report_key = (
            f"quality/snapshot_date={metadata['snapshot_date']}/{metadata['pipeline_run_id']}.json"
        )
        ObjectStore(settings).put_json(settings.curated_bucket, report_key, report.to_dict())
        return {**metadata, "validation_report": report.to_dict()}

    @task(retries=0)
    def quarantine_or_promote(metadata: dict[str, Any]) -> dict[str, Any]:
        settings = Settings()
        database = Database(settings.postgres_dsn)
        report = metadata["validation_report"]
        if report["blocking"]:
            ObjectStore(settings).copy(
                settings.raw_bucket,
                metadata["raw_key"],
                settings.quarantine_bucket,
                metadata["raw_key"],
            )
            database.update_status(
                metadata["pipeline_run_id"],
                ManifestStatus.QUARANTINED,
                "failed",
                json.dumps(report),
            )
            raise AirflowFailException("Blocking GTFS validation rules failed.")
        return metadata

    @task
    def dbt_build(metadata: dict[str, Any]) -> dict[str, Any]:
        project = os.getenv("DBT_PROJECT_DIR", "/workspace/transformations/dbt")
        subprocess.run(
            ["dbt", "build", "--project-dir", project, "--profiles-dir", project],
            check=True,
        )
        return metadata

    compute_diff = EmptyOperator(
        task_id="compute_diff",
        doc_md="`fact_network_change` is built and tested as part of the preceding dbt build.",
    )

    @task
    def publish_and_notify(metadata: dict[str, Any]) -> None:
        settings = Settings()
        Database(settings.postgres_dsn).update_status(
            metadata["pipeline_run_id"], ManifestStatus.PUBLISHED, "passed"
        )
        Path(metadata["local_path"]).unlink(missing_ok=True)

    checked = check_source()
    downloaded = download_and_hash(checked)
    registered = register_manifest(downloaded)
    validated = validate_gtfs(registered)
    promoted = quarantine_or_promote(validated)
    staged = extract_to_staging(promoted)
    built = dbt_build(staged)
    built >> compute_diff
    published = publish_and_notify(built)
    compute_diff >> published


gtfs_ingestion()
