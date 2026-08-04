"""Blocking and advisory GTFS contract validation."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from milano_mobility.gtfs import (
    REQUIRED_FILES,
    SUPPORTED_FILES,
    GTFSError,
    archive_members,
    gtfs_headers,
    iter_gtfs_rows,
    parse_gtfs_date,
    parse_gtfs_time,
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
    """Validate a GTFS feed with bounded memory and return every detected issue."""
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
        for filename, required_headers in HEADERS.items():
            if filename not in members:
                continue
            actual_headers = set(gtfs_headers(path, filename))
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
    except (GTFSError, UnicodeDecodeError) as error:
        report.issues.append(ValidationIssue("parseable_files", Severity.CRITICAL, str(error)))
        return report

    materialized_files = (
        "agency.txt",
        "stops.txt",
        "routes.txt",
        "calendar.txt",
        "calendar_dates.txt",
    )
    try:
        tables = {
            filename: list(iter_gtfs_rows(path, filename))
            for filename in materialized_files
            if filename in members
        }
    except (GTFSError, UnicodeDecodeError) as error:
        report.issues.append(ValidationIssue("parseable_files", Severity.CRITICAL, str(error)))
        return report

    for filename, rows in tables.items():
        report.row_counts[filename.removesuffix(".txt")] = len(rows)
    for filename in SUPPORTED_FILES:
        if filename in members:
            report.row_counts.setdefault(filename.removesuffix(".txt"), 0)

    _validate_primary_keys(tables, report)
    _validate_agencies(tables.get("agency.txt", []), report)
    _validate_stops(tables.get("stops.txt", []), report, invalid_coordinate_threshold)
    _validate_routes(tables.get("routes.txt", []), report)
    _validate_calendars(tables, report)

    agencies = {row.get("agency_id", "") for row in tables.get("agency.txt", [])}
    route_rows = tables.get("routes.txt", [])
    if not (len(agencies) == 1 and all(not row.get("agency_id") for row in route_rows)):
        _append_missing_references(
            report,
            "routes.txt",
            "agency_id",
            _missing_references(route_rows, "agency_id", agencies),
        )
    routes = {row.get("route_id", "") for row in route_rows}
    services = {
        row.get("service_id", "")
        for filename in ("calendar.txt", "calendar_dates.txt")
        for row in tables.get(filename, [])
    }

    try:
        trip_ordinals = _stream_trips(path, routes, services, report)
        _stream_stop_times(
            path,
            trip_ordinals,
            {row.get("stop_id", "") for row in tables.get("stops.txt", [])},
            report,
        )
    except (GTFSError, UnicodeDecodeError) as error:
        report.issues.append(ValidationIssue("parseable_files", Severity.CRITICAL, str(error)))
    return report


def _validate_primary_keys(
    tables: dict[str, list[dict[str, str]]], report: ValidationReport
) -> None:
    for filename, keys in PRIMARY_KEYS.items():
        if filename in {"trips.txt", "stop_times.txt"}:
            continue
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


def _stream_trips(
    path: Path,
    valid_routes: set[str],
    valid_services: set[str],
    report: ValidationReport,
) -> dict[str, int]:
    trip_ordinals: dict[str, int] = {}
    null_keys: list[str] = []
    null_key_count = 0
    duplicate_keys: list[str] = []
    duplicate_count = 0
    missing_routes: set[str] = set()
    missing_services: set[str] = set()
    row_count = 0
    for row in iter_gtfs_rows(path, "trips.txt"):
        row_count += 1
        trip_id = row.get("trip_id", "")
        if not trip_id:
            null_key_count += 1
            if len(null_keys) < 10:
                null_keys.append(trip_id)
        elif trip_id in trip_ordinals:
            duplicate_count += 1
            if len(duplicate_keys) < 10:
                duplicate_keys.append(trip_id)
        else:
            trip_ordinals[trip_id] = len(trip_ordinals)
        route_id = row.get("route_id", "")
        service_id = row.get("service_id", "")
        if route_id not in valid_routes:
            missing_routes.add(route_id)
        if service_id not in valid_services:
            missing_services.add(service_id)
    report.row_counts["trips"] = row_count
    if null_key_count:
        report.issues.append(
            ValidationIssue(
                "non_null_primary_key",
                Severity.CRITICAL,
                "trips.txt contains null logical keys.",
                "trips.txt",
                null_key_count,
                null_keys,
            )
        )
    if duplicate_count:
        report.issues.append(
            ValidationIssue(
                "unique_primary_key",
                Severity.CRITICAL,
                "trips.txt contains duplicate logical keys.",
                "trips.txt",
                duplicate_count,
                duplicate_keys,
            )
        )
    _append_missing_references(report, "trips.txt", "route_id", missing_routes)
    _append_missing_references(report, "trips.txt", "service_id", missing_services)
    return trip_ordinals


def _stream_stop_times(
    path: Path,
    trip_ordinals: dict[str, int],
    valid_stops: set[str],
    report: ValidationReport,
) -> None:
    unknown_trip_ordinals: dict[str, int] = {}
    seen_keys: set[int] = set()
    missing_trips: set[str] = set()
    missing_stops: set[str] = set()
    null_keys: list[str] = []
    null_key_count = 0
    duplicate_keys: list[str] = []
    duplicate_count = 0
    invalid_times: list[str] = []
    invalid_time_count = 0
    invalid_sequences: list[str] = []
    invalid_sequence_count = 0
    row_count = 0
    for row in iter_gtfs_rows(path, "stop_times.txt"):
        row_count += 1
        trip_id = row.get("trip_id", "")
        sequence_text = row.get("stop_sequence", "")
        key_text = f"{trip_id}|{sequence_text}"
        if not trip_id or not sequence_text:
            null_key_count += 1
        if (not trip_id or not sequence_text) and len(null_keys) < 10:
            null_keys.append(key_text)

        ordinal = trip_ordinals.get(trip_id)
        if ordinal is None:
            missing_trips.add(trip_id)
            ordinal = unknown_trip_ordinals.setdefault(
                trip_id, len(trip_ordinals) + len(unknown_trip_ordinals)
            )
        stop_id = row.get("stop_id", "")
        if stop_id not in valid_stops:
            missing_stops.add(stop_id)

        try:
            parse_gtfs_time(row.get("arrival_time", ""))
            parse_gtfs_time(row.get("departure_time", ""))
        except GTFSError:
            invalid_time_count += 1
            if len(invalid_times) < 10:
                invalid_times.append(key_text)

        try:
            sequence = int(sequence_text)
            if sequence < 0:
                invalid_sequence_count += 1
            if sequence < 0 and len(invalid_sequences) < 10:
                invalid_sequences.append(key_text)
            if sequence >= 0:
                pair_sum = ordinal + sequence
                encoded_key = pair_sum * (pair_sum + 1) // 2 + sequence
                if encoded_key in seen_keys:
                    duplicate_count += 1
                    if len(duplicate_keys) < 10:
                        duplicate_keys.append(key_text)
                else:
                    seen_keys.add(encoded_key)
        except ValueError:
            invalid_sequence_count += 1
            if len(invalid_sequences) < 10:
                invalid_sequences.append(key_text)

    report.row_counts["stop_times"] = row_count
    if null_key_count:
        report.issues.append(
            ValidationIssue(
                "non_null_primary_key",
                Severity.CRITICAL,
                "stop_times.txt contains null logical keys.",
                "stop_times.txt",
                null_key_count,
                null_keys,
            )
        )
    if duplicate_count:
        report.issues.append(
            ValidationIssue(
                "unique_primary_key",
                Severity.CRITICAL,
                "stop_times.txt contains duplicate logical keys.",
                "stop_times.txt",
                duplicate_count,
                duplicate_keys,
            )
        )
    _append_missing_references(report, "stop_times.txt", "trip_id", missing_trips)
    _append_missing_references(report, "stop_times.txt", "stop_id", missing_stops)
    if invalid_time_count:
        report.issues.append(
            ValidationIssue(
                "parseable_stop_times",
                Severity.CRITICAL,
                "Arrival and departure times must be valid GTFS times.",
                "stop_times.txt",
                invalid_time_count,
                invalid_times,
            )
        )
    if invalid_sequence_count:
        report.issues.append(
            ValidationIssue(
                "valid_stop_sequence",
                Severity.HIGH,
                "stop_sequence must be a non-negative integer.",
                "stop_times.txt",
                invalid_sequence_count,
                invalid_sequences,
            )
        )


def _append_missing_references(
    report: ValidationReport, filename: str, field: str, missing: set[str] | list[str]
) -> None:
    if missing:
        values = sorted(missing)
        report.issues.append(
            ValidationIssue(
                f"foreign_key_{field}",
                Severity.CRITICAL,
                f"{filename}.{field} contains unresolved references.",
                filename,
                len(values),
                values[:10],
            )
        )


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
