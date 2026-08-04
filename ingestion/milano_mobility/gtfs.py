"""GTFS parsing and deterministic hashing utilities."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

REQUIRED_FILES = frozenset({"agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt"})
SUPPORTED_FILES = (
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "shapes.txt",
    "stop_times.txt",
    "calendar.txt",
    "calendar_dates.txt",
)


class GTFSError(ValueError):
    """Raised when a GTFS value or archive cannot be parsed safely."""


def parse_gtfs_time(value: str) -> int:
    """Convert a GTFS HH:MM:SS value to seconds from the service-day start.

    GTFS explicitly permits hours greater than 23 for service after midnight.
    """
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise GTFSError(f"Invalid GTFS time: {value!r}")
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError as error:
        raise GTFSError(f"Invalid GTFS time: {value!r}") from error
    if hours < 0 or not 0 <= minutes <= 59 or not 0 <= seconds <= 59:
        raise GTFSError(f"Invalid GTFS time: {value!r}")
    return hours * 3600 + minutes * 60 + seconds


def parse_gtfs_date(value: str) -> str:
    """Convert YYYYMMDD to an ISO date string while validating the value."""
    from datetime import datetime

    try:
        return datetime.strptime(value.strip(), "%Y%m%d").date().isoformat()
    except ValueError as error:
        raise GTFSError(f"Invalid GTFS date: {value!r}") from error


def canonical_row_hash(row: Mapping[str, Any], excluded: set[str] | None = None) -> str:
    """Hash normalized row content independent of dictionary key order."""
    ignored = excluded or set()
    normalized = {
        key: "" if value is None else str(value).strip()
        for key, value in sorted(row.items())
        if key not in ignored
    }
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def file_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the payload into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_members(path: Path) -> set[str]:
    """Return safe, top-level filenames in a GTFS archive."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = set()
            for member in archive.infolist():
                name = member.filename
                if member.is_dir():
                    continue
                if "/" in name.replace("\\", "/") or name.startswith(("/", "\\")):
                    raise GTFSError(f"GTFS archive contains a non-top-level path: {name}")
                members.add(name)
            return members
    except zipfile.BadZipFile as error:
        raise GTFSError("Source payload is not a valid ZIP archive") from error


def iter_gtfs_rows(path: Path, filename: str) -> Iterator[dict[str, str]]:
    """Yield decoded rows from one GTFS CSV member."""
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.open(filename)
        except KeyError:
            return
        with raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.DictReader(text)
            if reader.fieldnames is None:
                raise GTFSError(f"{filename} has no header")
            for row in reader:
                yield {key.strip(): (value or "").strip() for key, value in row.items() if key}


def gtfs_headers(path: Path, filename: str) -> tuple[str, ...]:
    """Read a GTFS member's normalized header without materializing its rows."""
    with zipfile.ZipFile(path) as archive:
        try:
            raw = archive.open(filename)
        except KeyError:
            return ()
        with raw, io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            reader = csv.reader(text)
            try:
                return tuple(value.strip() for value in next(reader))
            except StopIteration as error:
                raise GTFSError(f"{filename} has no header") from error


def read_gtfs_tables(path: Path) -> dict[str, list[dict[str, str]]]:
    """Read all supported GTFS tables for validation or small fixtures."""
    members = archive_members(path)
    return {
        filename: list(iter_gtfs_rows(path, filename))
        for filename in SUPPORTED_FILES
        if filename in members
    }
