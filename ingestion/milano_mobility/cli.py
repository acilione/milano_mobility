"""Command-line interface for operators and automated jobs."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from milano_mobility.models import ManifestStatus
from milano_mobility.validation import validate_feed


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Date must use YYYY-MM-DD format") from error


def _local_today() -> date:
    return datetime.now(ZoneInfo(os.getenv("SERVICE_TIMEZONE", "UTC"))).date()


def parser() -> argparse.ArgumentParser:
    """Build the public CLI parser."""
    root = argparse.ArgumentParser(
        prog="mobility", description="Milano Mobility batch data platform"
    )
    commands = root.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Ingest and optionally publish one GTFS feed")
    run.add_argument("--feed", required=True, help="Local ZIP path or HTTP(S) URL")
    run.add_argument(
        "--snapshot-date",
        type=_date,
        default=_local_today(),
        help="Acquisition date in YYYY-MM-DD format (defaults to today)",
    )
    run.add_argument("--pipeline-run-id")
    run.add_argument("--build", action="store_true", help="Run dbt after a valid load")

    validate = commands.add_parser("validate", help="Validate a GTFS ZIP without loading it")
    validate.add_argument("--feed", required=True, type=Path)
    validate.add_argument("--snapshot-date", required=True, type=_date)
    validate.add_argument("--pipeline-run-id", default="local-validation")

    weather = commands.add_parser(
        "weather-backfill", help="Fetch and load daily weather enrichment"
    )
    weather.add_argument("--date-from", required=True, type=_date)
    weather.add_argument("--date-to", required=True, type=_date)
    return root


def main() -> None:
    """Execute a CLI command and use stable process exit codes."""
    arguments = parser().parse_args()
    if arguments.command == "validate":
        report = validate_feed(arguments.feed, arguments.pipeline_run_id, arguments.snapshot_date)
        print(json.dumps(report.to_dict(), indent=2))
        raise SystemExit(2 if report.blocking else 0)
    if arguments.command == "weather-backfill":
        from milano_mobility.weather import backfill_weather

        print(json.dumps(backfill_weather(arguments.date_from, arguments.date_to), indent=2))
        raise SystemExit(0)

    from milano_mobility.pipeline import (
        ValidationGateError,
        configure_logging,
        run_pipeline,
    )

    configure_logging()
    try:
        result = run_pipeline(
            arguments.feed,
            arguments.snapshot_date,
            pipeline_run_id=arguments.pipeline_run_id,
            build_warehouse=arguments.build,
        )
    except ValidationGateError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from error
    print(
        json.dumps(
            {
                "pipeline_run_id": result.pipeline_run_id,
                "status": result.status.value,
                "sha256": result.sha256,
                "object_uri": result.object_uri,
                "row_counts": result.row_counts,
                "validation_status": result.validation_status,
            },
            indent=2,
        )
    )
    raise SystemExit(0 if result.status is not ManifestStatus.FAILED else 1)


if __name__ == "__main__":
    main()
