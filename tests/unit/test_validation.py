from __future__ import annotations

from datetime import date
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from milano_mobility.models import Severity
from milano_mobility.validation import validate_feed

FIXTURES = Path(__file__).parents[1] / "fixtures"


def changed_feed(
    tmp_path: Path,
    *,
    replacements: dict[str, tuple[str, str]] | None = None,
    omitted: set[str] | None = None,
) -> Path:
    target = tmp_path / "changed.zip"
    changes = replacements or {}
    skipped = omitted or set()
    with (
        ZipFile(FIXTURES / "gtfs_v1.zip") as source,
        ZipFile(target, "w", ZIP_DEFLATED) as destination,
    ):
        for name in source.namelist():
            if name in skipped:
                continue
            value = source.read(name).decode("utf-8")
            if name in changes:
                value = value.replace(*changes[name])
            destination.writestr(name, value)
    return target


def test_valid_fixture_passes_all_blocking_checks() -> None:
    report = validate_feed(
        FIXTURES / "gtfs_v1.zip",
        "unit-valid",
        date(2026, 7, 28),
    )
    assert report.status == "passed"
    assert not report.blocking
    assert report.row_counts["stops"] == 3
    assert report.row_counts["stop_times"] == 6


def test_invalid_foreign_key_is_critical() -> None:
    report = validate_feed(
        FIXTURES / "gtfs_invalid_fk.zip",
        "unit-invalid",
        date(2026, 7, 28),
    )
    assert report.blocking
    assert report.status == "failed"
    assert any(
        issue.rule == "foreign_key_route_id" and issue.severity is Severity.CRITICAL
        for issue in report.issues
    )


def test_non_zip_payload_is_quarantinable(tmp_path: Path) -> None:
    payload = tmp_path / "not-a-feed.zip"
    payload.write_text("not a zip", encoding="utf-8")
    report = validate_feed(payload, "unit-bad-zip", date(2026, 7, 28))
    assert report.blocking
    assert report.issues[0].rule == "valid_zip"


def test_report_serialization_contains_gate_result() -> None:
    report = validate_feed(
        FIXTURES / "gtfs_v1.zip",
        "unit-json",
        date(2026, 7, 28),
    )
    value = report.to_dict()
    assert value["status"] == "passed"
    assert value["source_snapshot_date"] == "2026-07-28"
    assert value["pipeline_run_id"] == "unit-json"


@pytest.mark.parametrize(
    ("replacements", "omitted", "expected_rule"),
    [
        ({}, {"routes.txt"}, "required_files"),
        ({}, {"calendar.txt", "calendar_dates.txt"}, "service_calendar"),
        (
            {"stops.txt": ("\nS2,CENTRALE", "\nS1,CENTRALE")},
            set(),
            "unique_primary_key",
        ),
        (
            {"stops.txt": ("S1,DUOMO,Duomo,45.464170", "S1,DUOMO,Duomo,145.464170")},
            set(),
            "coordinate_range",
        ),
        (
            {"stops.txt": ("S1,DUOMO,Duomo", "S1,DUOMO,")},
            set(),
            "stop_name_not_null",
        ),
        (
            {"routes.txt": (",1,E51B23", ",99,E51B23")},
            set(),
            "accepted_route_type",
        ),
        (
            {"stop_times.txt": ("24:10:00", "24:70:00")},
            set(),
            "parseable_stop_times",
        ),
        (
            {
                "stop_times.txt": (
                    "T1,06:00:00,06:00:30,S3,1,,0,0\nT1,06:05:00,06:05:30,S1,2,,0,0",
                    "T1,06:05:00,06:05:30,S1,2,,0,0\nT1,06:00:00,06:00:30,S3,1,,0,0",
                )
            },
            set(),
            "increasing_stop_sequence",
        ),
        (
            {"agency.txt": ("Europe/Rome", "Invalid/Timezone")},
            set(),
            "valid_agency_timezone",
        ),
    ],
)
def test_contract_rules_block_invalid_feed(
    tmp_path: Path,
    replacements: dict[str, tuple[str, str]],
    omitted: set[str],
    expected_rule: str,
) -> None:
    feed = changed_feed(tmp_path, replacements=replacements, omitted=omitted)
    report = validate_feed(feed, f"contract-{expected_rule}", date(2026, 7, 28))
    assert report.blocking
    assert any(issue.rule == expected_rule for issue in report.issues)
