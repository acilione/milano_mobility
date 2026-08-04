"""Small read-only HTTP server for the mobility dashboard."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
import structlog
from psycopg.rows import dict_row

logger = structlog.get_logger()
STATIC_ROOT = files("milano_mobility").joinpath("static")
DASHBOARD_HTML = STATIC_ROOT.joinpath("index.html").read_text(encoding="utf-8")
STATIC_ASSETS = {
    "/assets/map.js": ("dist/map.js", "text/javascript; charset=utf-8"),
    "/assets/maplibre-gl.mjs": ("dist/maplibre-gl.mjs", "text/javascript; charset=utf-8"),
    "/assets/maplibre-gl-shared.mjs": (
        "dist/maplibre-gl-shared.mjs",
        "text/javascript; charset=utf-8",
    ),
    "/assets/maplibre-gl-worker.mjs": (
        "dist/maplibre-gl-worker.mjs",
        "text/javascript; charset=utf-8",
    ),
    "/assets/maplibre-gl.css": ("dist/maplibre-gl.css", "text/css; charset=utf-8"),
}
ROUTE_KEY_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _json_default(value: object) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def _static_asset(path: str) -> tuple[bytes, str] | None:
    """Read one allow-listed bundled frontend asset."""
    asset = STATIC_ASSETS.get(path)
    if asset is None:
        return None
    filename, content_type = asset
    try:
        return STATIC_ROOT.joinpath(filename).read_bytes(), content_type
    except FileNotFoundError:
        return None


def _database_parameters() -> dict[str, str | int]:
    return {
        "host": os.getenv("POSTGRES_HOST", "postgres"),
        "port": int(os.getenv("POSTGRES_PORT", "5432")),
        "dbname": os.getenv("POSTGRES_DB", "mobility"),
        "user": os.getenv("BI_DB_USER", "bi_reader"),
        "password": os.getenv("BI_DB_PASSWORD", "bi_reader_dev"),
        "connect_timeout": int(os.getenv("POSTGRES_CONNECT_TIMEOUT_SECONDS", "5")),
    }


def _dashboard_metadata() -> dict[str, str | int]:
    return {
        "title": os.getenv("DASHBOARD_TITLE", "Milano Mobility Observatory"),
        "area_name": os.getenv("SERVICE_AREA_NAME", "Milan"),
        "source_label": os.getenv("GTFS_SOURCE_LABEL", "Comune di Milano / AMAT"),
        "source_url": os.getenv("GTFS_SOURCE_URL", ""),
        "license": os.getenv("GTFS_LICENSE", "CC BY 4.0"),
        "refresh_seconds": int(os.getenv("DASHBOARD_REFRESH_SECONDS", "30")),
    }


def _normalize_route_ids(values: list[str]) -> list[str]:
    """Return unique warehouse route keys within a small request bound."""
    return list(dict.fromkeys(value for value in values if ROUTE_KEY_PATTERN.fullmatch(value)))[:50]


def load_route_shapes(route_ids: list[str]) -> list[dict[str, Any]]:
    """Load display-sized official GTFS paths for selected routes."""
    normalized_ids = _normalize_route_ids(route_ids)
    if not normalized_ids:
        return []
    try:
        parameters = _database_parameters()
        with psycopg.connect(
            host=str(parameters["host"]),
            port=int(parameters["port"]),
            dbname=str(parameters["dbname"]),
            user=str(parameters["user"]),
            password=str(parameters["password"]),
            connect_timeout=int(parameters["connect_timeout"]),
            row_factory=dict_row,
        ) as connection:
            return connection.execute(
                """
                WITH ordered_points AS (
                    SELECT
                        route_sk,
                        shape_id,
                        shape_pt_sequence,
                        shape_pt_lat,
                        shape_pt_lon,
                        row_number() OVER shape AS point_position,
                        count(*) OVER shape AS point_count
                    FROM marts.dashboard_route_shape
                    WHERE snapshot_date = (
                        SELECT max(snapshot_date) FROM marts.dashboard_route_shape
                    )
                      AND route_sk = ANY(%s)
                    WINDOW shape AS (
                        PARTITION BY route_sk, shape_id ORDER BY shape_pt_sequence
                    )
                ),
                sampled_points AS (
                    SELECT *
                    FROM ordered_points
                    WHERE point_position IN (1, point_count)
                       OR mod(
                           point_position - 1,
                           greatest(ceil(point_count / 120.0)::integer, 1)
                       ) = 0
                )
                SELECT
                    route_sk,
                    shape_id,
                    jsonb_agg(
                        jsonb_build_array(shape_pt_lon, shape_pt_lat)
                        ORDER BY shape_pt_sequence
                    ) AS points
                FROM sampled_points
                GROUP BY route_sk, shape_id
                ORDER BY route_sk, shape_id
                """,
                (normalized_ids,),
            ).fetchall()
    except psycopg.Error as error:
        logger.info("route_shapes_unavailable", reason=str(error).splitlines()[0])
        return []


def load_dashboard_data() -> dict[str, Any]:
    """Read the latest published snapshot through the read-only BI account."""
    metadata = _dashboard_metadata()
    try:
        parameters = _database_parameters()
        with psycopg.connect(
            host=str(parameters["host"]),
            port=int(parameters["port"]),
            dbname=str(parameters["dbname"]),
            user=str(parameters["user"]),
            password=str(parameters["password"]),
            connect_timeout=int(parameters["connect_timeout"]),
            row_factory=dict_row,
        ) as connection:
            summary = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT max(snapshot_date) AS snapshot_date
                    FROM marts.dashboard_service_activity
                )
                SELECT
                    latest.snapshot_date,
                    sum(service.scheduled_trips) AS scheduled_trips,
                    count(DISTINCT service.service_date) AS service_days,
                    (
                        SELECT sum(stop.stop_events)
                        FROM marts.dashboard_stop_activity AS stop
                        WHERE stop.snapshot_date = latest.snapshot_date
                    ) AS stop_events,
                    (
                        SELECT count(DISTINCT stop.stop_sk)
                        FROM marts.dashboard_stop_activity AS stop
                        WHERE stop.snapshot_date = latest.snapshot_date
                    ) AS active_stops,
                    count(DISTINCT service.route_sk) AS active_routes
                FROM marts.dashboard_service_activity AS service
                CROSS JOIN latest_snapshot AS latest
                WHERE service.snapshot_date = latest.snapshot_date
                GROUP BY latest.snapshot_date
                """
            ).fetchone()
            departures = connection.execute(
                """
                SELECT service_hour, sum(scheduled_trips) AS departures
                FROM marts.dashboard_service_activity
                WHERE snapshot_date = (
                    SELECT max(snapshot_date) FROM marts.dashboard_service_activity
                )
                  AND service_hour IS NOT NULL
                GROUP BY 1
                ORDER BY 1
                """
            ).fetchall()
            service_days = connection.execute(
                """
                SELECT
                    calendar.date AS service_date,
                    calendar.weekday_name,
                    calendar.is_weekend,
                    weather.temperature_max_c,
                    weather.precipitation_mm,
                    coalesce(sum(service.scheduled_trips), 0) AS scheduled_trips
                FROM marts.dim_date AS calendar
                LEFT JOIN marts.dim_weather_day AS weather USING (date_key)
                LEFT JOIN marts.dashboard_service_activity AS service USING (date_key)
                WHERE calendar.date BETWEEN
                    (SELECT min(service_date) FROM marts.dashboard_service_activity)
                    AND (SELECT max(service_date) FROM marts.dashboard_service_activity)
                  AND service.snapshot_date = (
                      SELECT max(snapshot_date) FROM marts.dashboard_service_activity
                  )
                GROUP BY 1, 2, 3, 4, 5
                ORDER BY 1
                """
            ).fetchall()
            route_coverage = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT max(snapshot_date) AS snapshot_date
                    FROM marts.dashboard_service_activity
                ),
                service_by_route AS (
                    SELECT route_sk, sum(scheduled_trips) AS trips
                    FROM marts.dashboard_service_activity AS service
                    CROSS JOIN latest_snapshot
                    WHERE service.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY route_sk
                ),
                stops_by_route AS (
                    SELECT
                        route_sk,
                        count(DISTINCT stop_sk) AS served_stops,
                        sum(stop_events) AS stop_events
                    FROM marts.dashboard_stop_activity AS stop
                    CROSS JOIN latest_snapshot
                    WHERE stop.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY route_sk
                )
                SELECT
                    route.route_sk,
                    coalesce(route.route_short_name, route.route_id) AS route_name,
                    route.route_long_name,
                    route.route_color,
                    route.route_type,
                    stops.served_stops,
                    service.trips,
                    stops.stop_events
                FROM service_by_route AS service
                JOIN stops_by_route AS stops USING (route_sk)
                JOIN marts.dim_route AS route USING (route_sk)
                ORDER BY route.route_type, route.route_short_name, route.route_id
                """
            ).fetchall()
            changes = connection.execute(
                """
                SELECT entity_type, change_type, count(*) AS changed_entities
                FROM marts.fact_network_change
                WHERE snapshot_date = (
                    SELECT max(snapshot_date) FROM marts.fact_network_change
                )
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            ).fetchall()
            stops = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT max(snapshot_date) AS snapshot_date
                    FROM marts.dashboard_stop_activity
                ),
                stop_activity AS (
                    SELECT stop_sk, sum(stop_events) AS stop_events
                    FROM marts.dashboard_stop_activity AS activity
                    CROSS JOIN latest_snapshot
                    WHERE activity.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY stop_sk
                ),
                connected_routes AS (
                    SELECT
                        activity.stop_sk,
                        jsonb_agg(
                            jsonb_build_object(
                                'route_sk', route.route_sk,
                                'route_name', coalesce(route.route_short_name, route.route_id),
                                'route_long_name', route.route_long_name,
                                'route_color', route.route_color,
                                'route_type', route.route_type
                            ) ORDER BY route.route_type, route.route_short_name, route.route_id
                        ) AS routes
                    FROM marts.dashboard_stop_activity AS activity
                    CROSS JOIN latest_snapshot
                    JOIN marts.dim_route AS route USING (route_sk)
                    WHERE activity.snapshot_date = latest_snapshot.snapshot_date
                    GROUP BY activity.stop_sk
                )
                SELECT
                    stop.stop_id,
                    stop.stop_name,
                    stop.stop_lat,
                    stop.stop_lon,
                    activity.stop_events,
                    routes.routes
                FROM stop_activity AS activity
                JOIN connected_routes AS routes USING (stop_sk)
                JOIN marts.dim_stop AS stop USING (stop_sk)
                ORDER BY activity.stop_events DESC, stop.stop_name
                """
            ).fetchall()
        return {
            "state": "ready",
            "generated_at": datetime.now().astimezone(),
            "meta": metadata,
            "summary": summary or {},
            "departures": departures,
            "service_days": service_days,
            "route_coverage": route_coverage,
            "changes": changes,
            "stops": stops,
        }
    except (psycopg.Error, KeyError) as error:
        logger.info("dashboard_waiting_for_marts", reason=str(error).splitlines()[0])
        return {
            "state": "waiting",
            "generated_at": datetime.now().astimezone(),
            "meta": metadata,
            "message": "The dashboard is waiting for its first published snapshot.",
        }


class DashboardHandler(BaseHTTPRequestHandler):
    """Serve the dashboard shell and its read-only JSON data."""

    server_version = "MobilityDashboard/1.0"

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path in {"/", "/index.html"}:
            self._send(HTTPStatus.OK, DASHBOARD_HTML, "text/html; charset=utf-8")
            return
        asset = _static_asset(request.path)
        if asset is not None:
            body, content_type = asset
            self._send(HTTPStatus.OK, body, content_type)
            return
        if request.path == "/api/dashboard":
            payload = json.dumps(load_dashboard_data(), default=_json_default).encode()
            self._send(HTTPStatus.OK, payload, "application/json")
            return
        if request.path == "/api/route-shapes":
            route_ids = parse_qs(request.query).get("route_sk", [])
            shape_payload = json.dumps(
                {"routes": load_route_shapes(route_ids)}, default=_json_default
            )
            self._send(HTTPStatus.OK, shape_payload, "application/json")
            return
        if request.path == "/health":
            self._send(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
            return
        self._send(HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json")

    def log_message(self, message_format: str, *args: object) -> None:
        logger.info("dashboard_request", message=message_format % args)

    def _send(self, status: HTTPStatus, body: str | bytes, content_type: str) -> None:
        encoded = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    """Run the local dashboard server."""
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8501"))
    logger.info("dashboard_started", host=host, port=port)
    ThreadingHTTPServer((host, port), DashboardHandler).serve_forever()


if __name__ == "__main__":
    main()
