"""Blocking and advisory GTFS contract validation."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from milano_mobility.gtfs import (
    REQUIRED_FILES,
    GTFSError,
    archive_members,
    parse_gtfs_date,
    parse_gtfs_time,
    read_gtfs_tables,
)
from milano_mobility.models import Severity, ValidationIssue, ValidationReport

HEADERS: dict[str, set[str]] = {
    "agency.txt": {"agency_name", "agency_url", "agency_timezone"},
    "stops.txt": {"stop_id", "stop_name", "stop_lat", "stop_lon"},
    "routes.txt": {"route_id", "route_type"},
    "trips.txt": {"route_id", "service_id", "trip_id"},
    "stop_times.txt": {
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
    },
    "calendar.txt": {
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    },
    "calendar_dates.txt": {"service_id", "date", "exception_type"},
}

PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "agency.txt": ("agency_id",),
    "stops.txt": ("stop_id",),
    "routes.txt": ("route_id",),
    "trips.txt": ("trip_id",),
    "stop_times.txt": ("trip_id", "stop_sequence"),
    "calendar.txt": ("service_id",),
    "calendar_dates.txt": ("service_id", "date"),
}


def _duplicates(rows: list[dict[str, str]], keys: tuple[str, ...]) -> list[str]:
    values = [tuple(row.get(key, "") for key in keys) for row in rows]
    return ["|".join(value) for value, count in Counter(values).items() if count > 1]


def _missing_references(
    rows: list[dict[str, str]], field: str, valid_values: set[str]
) -> list[str]:
    return sorted({row.get(field, "") for row in rows if row.get(field, "") not in valid_values})


def validate_feed(
    path: Path,
    pipeline_run_id: str,
    snapshot_date: date,
    invalid_coordinate_threshold: float = 0.005,
) -> ValidationReport:
    """Validate a GTFS feed and return every detected issue."""
    report = ValidationReport(pipeline_run_id=pipeline_run_id, source_snapshot_date=snapshot_date)
    try:
        members = archive_members(path)
    except GTFSError as error:
        report.issues.append(ValidationIssue("valid_zip", Severity.CRITICAL, str(error), count=1))
        return report

    missing = sorted(REQUIRED_FILES - members)
    if missing:
        report.issues.append(
            ValidationIssue(
                "required_files",
                Severity.CRITICAL,
                f"Required GTFS files are missing: {', '.join(missing)}",
                count=len(missing),
                sample=missing[:10],
            )
        )
        return report
    if not {"calendar.txt", "calendar_dates.txt"} & members:
        report.issues.append(
            ValidationIssue(
                "service_calendar",
                Severity.CRITICAL,
                "At least one of calendar.txt or calendar_dates.txt is required.",
            )
        )

    try:
        tables = read_gtfs_tables(path)
    except (GTFSError, UnicodeDecodeError) as error:
        report.issues.append(ValidationIssue("parseable_files", Severity.CRITICAL, str(error)))
        return report

    report.row_counts = {name.removesuffix(".txt"): len(rows) for name, rows in tables.items()}
    for filename, required_headers in HEADERS.items():
        if filename not in tables:
            continue
        actual_headers = set(tables[filename][0]) if tables[filename] else set()
        missing_headers = sorted(required_headers - actual_headers)
        if missing_headers:
            report.issues.append(
                ValidationIssue(
                    "required_columns",
                    Severity.CRITICAL,
                    f"{filename} is missing columns: {', '.join(missing_headers)}",
                    table=filename,
                    count=len(missing_headers),
                    sample=missing_headers,
                )
            )

    for filename, keys in PRIMARY_KEYS.items():
        rows = tables.get(filename)
        if rows is None:
            continue
        if filename == "agency.txt" and rows and "agency_id" not in rows[0]:
            continue
        null_keys = [
            "|".join(row.get(key, "") for key in keys)
            for row in rows
            if any(not row.get(key) for key in keys)
        ]
        duplicates = _duplicates(rows, keys)
        if null_keys:
            report.issues.append(
                ValidationIssue(
                    "non_null_primary_key",
                    Severity.CRITICAL,
                    f"{filename} contains null logical keys.",
                    filename,
                    len(null_keys),
                    null_keys[:10],
                )
            )
        if duplicates:
            report.issues.append(
                ValidationIssue(
                    "unique_primary_key",
                    Severity.CRITICAL,
                    f"{filename} contains duplicate logical keys.",
                    filename,
                    len(duplicates),
                    duplicates[:10],
                )
            )

    _validate_references(tables, report)
    _validate_agencies(tables.get("agency.txt", []), report)
    _validate_stops(tables.get("stops.txt", []), report, invalid_coordinate_threshold)
    _validate_stop_times(tables.get("stop_times.txt", []), report)
    _validate_routes(tables.get("routes.txt", []), report)
    _validate_calendars(tables, report)
    return report


def _validate_agencies(rows: list[dict[str, str]], report: ValidationReport) -> None:
    invalid: list[str] = []
    for row in rows:
        try:
            ZoneInfo(row.get("agency_timezone", ""))
        except (ValueError, ZoneInfoNotFoundError):
            invalid.append(row.get("agency_id", "default"))
    if invalid:
        report.issues.append(
            ValidationIssue(
                "valid_agency_timezone",
                Severity.CRITICAL,
                "agency_timezone must be a valid IANA time zone.",
                "agency.txt",
                len(invalid),
                invalid[:10],
            )
        )


def _validate_references(tables: dict[str, list[dict[str, str]]], report: ValidationReport) -> None:
    agencies = {row.get("agency_id", "") for row in tables.get("agency.txt", [])}
    routes = {row.get("route_id", "") for row in tables.get("routes.txt", [])}
    trips = {row.get("trip_id", "") for row in tables.get("trips.txt", [])}
    stops = {row.get("stop_id", "") for row in tables.get("stops.txt", [])}
    services = {
        row.get("service_id", "")
        for filename in ("calendar.txt", "calendar_dates.txt")
        for row in tables.get(filename, [])
    }
    checks = [
        ("routes.txt", "agency_id", agencies, tables.get("routes.txt", [])),
        ("trips.txt", "route_id", routes, tables.get("trips.txt", [])),
        ("trips.txt", "service_id", services, tables.get("trips.txt", [])),
        ("stop_times.txt", "trip_id", trips, tables.get("stop_times.txt", [])),
        ("stop_times.txt", "stop_id", stops, tables.get("stop_times.txt", [])),
    ]
    for filename, field, valid, rows in checks:
        if field == "agency_id" and all(not row.get(field) for row in rows) and len(agencies) == 1:
            continue
        missing = _missing_references(rows, field, valid)
        if missing:
            report.issues.append(
                ValidationIssue(
                    f"foreign_key_{field}",
                    Severity.CRITICAL,
                    f"{filename}.{field} contains unresolved references.",
                    filename,
                    len(missing),
                    missing[:10],
                )
            )


def _validate_stops(rows: list[dict[str, str]], report: ValidationReport, threshold: float) -> None:
    invalid: list[str] = []
    missing_names: list[str] = []
    for row in rows:
        stop_id = row.get("stop_id", "")
        if not row.get("stop_name"):
            missing_names.append(stop_id)
        try:
            latitude = float(row.get("stop_lat", ""))
            longitude = float(row.get("stop_lon", ""))
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                invalid.append(stop_id)
        except ValueError:
            invalid.append(stop_id)
    if missing_names:
        report.issues.append(
            ValidationIssue(
                "stop_name_not_null",
                Severity.CRITICAL,
                "Stops must have a name.",
                "stops.txt",
                len(missing_names),
                missing_names[:10],
            )
        )
    if invalid:
        ratio = len(invalid) / max(len(rows), 1)
        severity = Severity.HIGH if ratio > threshold else Severity.MEDIUM
        report.issues.append(
            ValidationIssue(
                "coordinate_range",
                severity,
                f"{len(invalid)} stops have invalid coordinates ({ratio:.2%}).",
                "stops.txt",
                len(invalid),
                invalid[:10],
            )
        )


def _validate_stop_times(rows: list[dict[str, str]], report: ValidationReport) -> None:
    invalid_times: list[str] = []
    invalid_sequences: list[str] = []
    last_sequence: dict[str, int] = {}
    for row in rows:
        key = f"{row.get('trip_id', '')}|{row.get('stop_sequence', '')}"
        try:
            parse_gtfs_time(row.get("arrival_time", ""))
            parse_gtfs_time(row.get("departure_time", ""))
        except GTFSError:
            invalid_times.append(key)
        try:
            sequence = int(row.get("stop_sequence", ""))
            trip_id = row.get("trip_id", "")
            if sequence <= last_sequence.get(trip_id, -1):
                invalid_sequences.append(key)
            last_sequence[trip_id] = sequence
        except ValueError:
            invalid_sequences.append(key)
    if invalid_times:
        report.issues.append(
            ValidationIssue(
                "parseable_stop_times",
                Severity.CRITICAL,
                "Arrival and departure times must be valid GTFS times.",
                "stop_times.txt",
                len(invalid_times),
                invalid_times[:10],
            )
        )
    if invalid_sequences:
        report.issues.append(
            ValidationIssue(
                "increasing_stop_sequence",
                Severity.HIGH,
                "stop_sequence must increase within each trip.",
                "stop_times.txt",
                len(invalid_sequences),
                invalid_sequences[:10],
            )
        )


def _validate_routes(rows: list[dict[str, str]], report: ValidationReport) -> None:
    invalid = [
        row.get("route_id", "")
        for row in rows
        if row.get("route_type", "") not in {str(value) for value in range(13)}
    ]
    if invalid:
        report.issues.append(
            ValidationIssue(
                "accepted_route_type",
                Severity.CRITICAL,
                "route_type must be a GTFS-defined integer from 0 through 12.",
                "routes.txt",
                len(invalid),
                invalid[:10],
            )
        )


def _validate_calendars(tables: dict[str, list[dict[str, str]]], report: ValidationReport) -> None:
    invalid: list[str] = []
    for row in tables.get("calendar.txt", []):
        try:
            start = parse_gtfs_date(row.get("start_date", ""))
            end = parse_gtfs_date(row.get("end_date", ""))
            if start > end:
                invalid.append(row.get("service_id", ""))
        except GTFSError:
            invalid.append(row.get("service_id", ""))
    for row in tables.get("calendar_dates.txt", []):
        try:
            parse_gtfs_date(row.get("date", ""))
            if row.get("exception_type") not in {"1", "2"}:
                invalid.append(row.get("service_id", ""))
        except GTFSError:
            invalid.append(row.get("service_id", ""))
    if invalid:
        report.issues.append(
            ValidationIssue(
                "valid_service_calendar",
                Severity.CRITICAL,
                "Service calendar dates or exceptions are invalid.",
                "calendar.txt",
                len(invalid),
                invalid[:10],
            )
        )
