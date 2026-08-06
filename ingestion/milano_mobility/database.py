"""PostgreSQL manifest registration and bulk staging loads."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection, sql
from psycopg.rows import dict_row

from milano_mobility.gtfs import (
    canonical_row_hash,
    iter_gtfs_rows,
    parse_gtfs_date,
    parse_gtfs_time,
)
from milano_mobility.models import Manifest, ManifestStatus

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "agency": (
        "agency_id",
        "agency_name",
        "agency_url",
        "agency_timezone",
        "agency_lang",
    ),
    "stops": (
        "stop_id",
        "stop_code",
        "stop_name",
        "stop_desc",
        "stop_lat",
        "stop_lon",
        "zone_id",
        "location_type",
        "parent_station",
    ),
    "routes": (
        "route_id",
        "agency_id",
        "route_short_name",
        "route_long_name",
        "route_desc",
        "route_type",
        "route_color",
        "route_text_color",
    ),
    "trips": (
        "route_id",
        "service_id",
        "trip_id",
        "trip_headsign",
        "direction_id",
        "block_id",
        "shape_id",
    ),
    "shapes": (
        "shape_id",
        "shape_pt_lat",
        "shape_pt_lon",
        "shape_pt_sequence",
        "shape_dist_traveled",
    ),
    "stop_times": (
        "trip_id",
        "arrival_seconds",
        "departure_seconds",
        "stop_id",
        "stop_sequence",
        "stop_headsign",
        "pickup_type",
        "drop_off_type",
    ),
    "calendar": (
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
    ),
    "calendar_dates": ("service_id", "service_date", "exception_type"),
}


class Database:
    """Database operations kept deliberately small and transaction-oriented."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def connect(self) -> Connection[Any]:
        """Open a connection whose caller controls the transaction."""
        return psycopg.connect(self._dsn)

    def find_duplicate(self, source: str, sha256: str) -> dict[str, Any] | None:
        """Find an already accepted payload by its natural ingestion key."""
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            return connection.execute(
                """
                SELECT pipeline_run_id, status, object_uri
                FROM audit.ingestion_manifest
                WHERE source = %s AND sha256 = %s
                  AND status IN ('VALIDATED', 'PUBLISHED', 'SKIPPED')
                ORDER BY retrieved_at DESC
                LIMIT 1
                """,
                (source, sha256),
            ).fetchone()

    def find_source_version(
        self, source: str, http_etag: str | None, http_last_modified: str | None
    ) -> dict[str, Any] | None:
        """Find an accepted source version using advisory HTTP discovery metadata."""
        if not http_etag and not http_last_modified:
            return None
        with psycopg.connect(self._dsn, row_factory=dict_row) as connection:
            return connection.execute(
                """
                SELECT pipeline_run_id, status, object_uri, sha256
                FROM audit.ingestion_manifest
                WHERE source = %s
                  AND status IN ('VALIDATED', 'PUBLISHED', 'SKIPPED')
                  AND (
                      (%s::text IS NOT NULL AND http_etag = %s::text)
                      OR (
                          %s::text IS NULL
                          AND http_last_modified = %s::text
                      )
                  )
                ORDER BY retrieved_at DESC
                LIMIT 1
                """,
                (
                    source,
                    http_etag,
                    http_etag,
                    http_etag,
                    http_last_modified,
                ),
            ).fetchone()

    def register_manifest(self, manifest: Manifest) -> None:
        """Register one RECEIVED payload."""
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                """
                INSERT INTO audit.ingestion_manifest (
                    pipeline_run_id, source, retrieved_at, effective_snapshot_date,
                    object_uri, sha256, http_etag, http_last_modified, bytes,
                    validator_status, status
                ) VALUES (
                    %(pipeline_run_id)s, %(source)s, %(retrieved_at)s,
                    %(effective_snapshot_date)s, %(object_uri)s, %(sha256)s,
                    %(http_etag)s, %(http_last_modified)s, %(bytes)s,
                    %(validator_status)s, %(status)s
                )
                """,
                manifest.to_dict(),
            )

    def update_status(
        self,
        pipeline_run_id: str,
        status: ManifestStatus,
        validator_status: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Move a manifest to a new lifecycle status."""
        with psycopg.connect(self._dsn) as connection:
            connection.execute(
                """
                UPDATE audit.ingestion_manifest
                SET status = %s,
                    validator_status = COALESCE(%s, validator_status),
                    error_message = %s,
                    updated_at = now()
                WHERE pipeline_run_id = %s
                """,
                (status.value, validator_status, error_message, pipeline_run_id),
            )

    def load_staging(
        self, feed_path: Path, snapshot_date: date, pipeline_run_id: str
    ) -> dict[str, int]:
        """Atomically replace this run's staging partition and bulk load GTFS rows."""
        counts: dict[str, int] = {}
        with psycopg.connect(self._dsn) as connection:
            for table, columns in TABLE_COLUMNS.items():
                filename = f"{table}.txt"
                connection.execute(
                    sql.SQL("DELETE FROM staging.{} WHERE source_snapshot_date = %s").format(
                        sql.Identifier(table)
                    ),
                    (snapshot_date,),
                )
                audit_columns = ("source_snapshot_date", "pipeline_run_id", "loaded_at")
                copy_columns = (*columns, "entity_hash", *audit_columns)
                statement = sql.SQL("COPY staging.{} ({}) FROM STDIN").format(
                    sql.Identifier(table),
                    sql.SQL(", ").join(map(sql.Identifier, copy_columns)),
                )
                row_count = 0
                with connection.cursor().copy(statement) as copy:
                    for row in iter_gtfs_rows(feed_path, filename):
                        transformed = _transform_row(table, row, columns)
                        values = [transformed.get(column) for column in columns]
                        copy.write_row(
                            (
                                *values,
                                canonical_row_hash(row),
                                snapshot_date,
                                pipeline_run_id,
                                "now",
                            )
                        )
                        row_count += 1
                counts[table] = row_count
        # PostgreSQL's automatic statistics collection can start after dbt has already
        # planned its first joins on a newly loaded multi-million-row feed. Collecting
        # statistics synchronously keeps the one-command build predictable.
        with psycopg.connect(self._dsn) as connection:
            for table in TABLE_COLUMNS:
                connection.execute(sql.SQL("ANALYZE staging.{}").format(sql.Identifier(table)))
        return counts


def _transform_row(
    table: str, row: dict[str, str], columns: Iterable[str]
) -> dict[str, object | None]:
    values: dict[str, object | None] = {column: row.get(column) or None for column in columns}
    if table == "agency" and not values.get("agency_id"):
        values["agency_id"] = "default"
    if table == "stop_times":
        values["arrival_seconds"] = parse_gtfs_time(row["arrival_time"])
        values["departure_seconds"] = parse_gtfs_time(row["departure_time"])
    if table == "calendar":
        values["start_date"] = parse_gtfs_date(row["start_date"])
        values["end_date"] = parse_gtfs_date(row["end_date"])
    if table == "calendar_dates":
        values["service_date"] = parse_gtfs_date(row["date"])
    return values
