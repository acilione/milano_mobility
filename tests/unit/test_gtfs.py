from __future__ import annotations

from pathlib import Path

import pytest

from milano_mobility.gtfs import (
    GTFSError,
    canonical_row_hash,
    file_sha256,
    parse_gtfs_date,
    parse_gtfs_time,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00:00:00", 0),
        ("06:05:30", 21_930),
        ("24:10:00", 87_000),
        ("27:59:59", 100_799),
    ],
)
def test_parse_gtfs_time_supports_service_day_hours(value: str, expected: int) -> None:
    assert parse_gtfs_time(value) == expected


@pytest.mark.parametrize("value", ["", "12:30", "12:60:00", "-1:00:00", "noon"])
def test_parse_gtfs_time_rejects_invalid_values(value: str) -> None:
    with pytest.raises(GTFSError):
        parse_gtfs_time(value)


def test_parse_gtfs_date_returns_iso_value() -> None:
    assert parse_gtfs_date("20260728") == "2026-07-28"


def test_parse_gtfs_date_rejects_impossible_date() -> None:
    with pytest.raises(GTFSError):
        parse_gtfs_date("20260230")


def test_canonical_hash_is_independent_of_key_order_and_whitespace() -> None:
    first = {"stop_name": " Duomo ", "stop_id": "S1"}
    second = {"stop_id": "S1", "stop_name": "Duomo"}
    assert canonical_row_hash(first) == canonical_row_hash(second)


def test_canonical_hash_changes_with_business_content() -> None:
    assert canonical_row_hash({"stop_id": "S1"}) != canonical_row_hash({"stop_id": "S2"})


def test_file_hash_is_stable(tmp_path: Path) -> None:
    payload = tmp_path / "payload.zip"
    payload.write_bytes(b"mobility")
    assert file_sha256(payload) == (
        "9377b038eaec24cab37359e550d2f410c0623949264413b30770df83f6fd5164"
    )
