"""Small read-only HTTP server for the mobility dashboard."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Event, Lock, Thread
from typing import Any
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import psycopg
import structlog
from psycopg.rows import dict_row

from milano_mobility.config import Settings
from milano_mobility.models import ManifestStatus, PipelineResult

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
_REFRESH_LOCK = Lock()
_REFRESH_CANCEL = Event()
_REFRESH_STATE: dict[str, Any] = {
    "state": "idle",
    "stage": "idle",
    "progress": 0,
    "message": "Ready to download the latest snapshot",
    "pipeline_run_id": None,
    "snapshot_date": None,
    "started_at": None,
    "completed_at": None,
}


class RefreshCancelled(RuntimeError):
    """Raised cooperatively when the dashboard user cancels a feed download."""


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


def refresh_status() -> dict[str, Any]:
    """Return an isolated copy of the in-process refresh state."""
    with _REFRESH_LOCK:
        return dict(_REFRESH_STATE)


def _update_refresh(**values: object) -> None:
    with _REFRESH_LOCK:
        _REFRESH_STATE.update(values)


def _execute_pipeline(
    feed: str,
    snapshot_date: date,
    *,
    settings: Settings,
    pipeline_run_id: str,
    build_warehouse: bool,
    progress_callback: Callable[[str, int, str], None],
) -> PipelineResult:
    """Load the heavier ingestion dependencies only when a refresh starts."""
    from milano_mobility.pipeline import run_pipeline

    return run_pipeline(
        feed,
        snapshot_date,
        settings=settings,
        pipeline_run_id=pipeline_run_id,
        build_warehouse=build_warehouse,
        progress_callback=progress_callback,
    )


def _run_refresh(run_id: str) -> None:
    settings = Settings()
    snapshot_date = datetime.now(ZoneInfo(settings.service_timezone)).date()

    def progress(stage: str, percentage: int, message: str) -> None:
        if _REFRESH_CANCEL.is_set():
            raise RefreshCancelled
        _update_refresh(stage=stage, progress=percentage, message=message)

    try:
        if not settings.source_url:
            raise ValueError("GTFS_SOURCE_URL is not configured")
        result = _execute_pipeline(
            settings.source_url,
            snapshot_date,
            settings=settings,
            pipeline_run_id=run_id,
            build_warehouse=True,
            progress_callback=progress,
        )
        unchanged = result.status is ManifestStatus.SKIPPED
        _update_refresh(
            state="unchanged" if unchanged else "succeeded",
            stage="unchanged" if unchanged else "published",
            progress=100,
            message=(
                "The latest snapshot is already downloaded"
                if unchanged
                else "The latest snapshot is published"
            ),
            completed_at=datetime.now().astimezone().isoformat(),
        )
    except RefreshCancelled:
        _update_refresh(
            state="cancelled",
            stage="cancelled",
            message="The update was cancelled; the published snapshot was not changed",
            completed_at=datetime.now().astimezone().isoformat(),
        )
    except Exception as error:
        logger.exception("dashboard_refresh_failed", pipeline_run_id=run_id)
        _update_refresh(
            state="failed",
            stage="failed",
            message=str(error).splitlines()[0] or type(error).__name__,
            completed_at=datetime.now().astimezone().isoformat(),
        )


def start_refresh() -> tuple[bool, dict[str, Any]]:
    """Start one background full-feed refresh, rejecting concurrent requests."""
    with _REFRESH_LOCK:
        if _REFRESH_STATE["state"] == "running":
            return False, dict(_REFRESH_STATE)
        run_id = str(uuid.uuid4())
        settings = Settings()
        _REFRESH_CANCEL.clear()
        _REFRESH_STATE.update(
            state="running",
            stage="starting",
            progress=1,
            message="Starting the full snapshot download",
            pipeline_run_id=run_id,
            snapshot_date=datetime.now(ZoneInfo(settings.service_timezone)).date().isoformat(),
            started_at=datetime.now().astimezone().isoformat(),
            completed_at=None,
        )
        state = dict(_REFRESH_STATE)
    Thread(target=_run_refresh, args=(run_id,), name="gtfs-dashboard-refresh", daemon=True).start()
    return True, state


def cancel_refresh() -> tuple[bool, dict[str, Any]]:
    """Request cancellation while source checking or download is still active."""
    with _REFRESH_LOCK:
        cancellable_stages = {"starting", "checking", "downloading"}
        if (
            _REFRESH_STATE["state"] != "running"
            or _REFRESH_STATE["stage"] not in cancellable_stages
        ):
            return False, dict(_REFRESH_STATE)
        _REFRESH_CANCEL.set()
        _REFRESH_STATE.update(
            stage="cancelling",
            message="Cancelling the current download",
        )
        return True, dict(_REFRESH_STATE)


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
                        SELECT snapshot_date FROM marts.published_snapshot
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
                    SELECT snapshot_date FROM marts.published_snapshot
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
                    SELECT snapshot_date FROM marts.published_snapshot
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
                    (
                        SELECT min(service_date)
                        FROM marts.dashboard_service_activity
                        WHERE snapshot_date = (
                            SELECT snapshot_date FROM marts.published_snapshot
                        )
                    )
                    AND (
                        SELECT max(service_date)
                        FROM marts.dashboard_service_activity
                        WHERE snapshot_date = (
                            SELECT snapshot_date FROM marts.published_snapshot
                        )
                    )
                  AND service.snapshot_date = (
                      SELECT snapshot_date FROM marts.published_snapshot
                  )
                GROUP BY 1, 2, 3, 4, 5
                ORDER BY 1
                """
            ).fetchall()
            route_coverage = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT snapshot_date FROM marts.published_snapshot
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
                    SELECT snapshot_date FROM marts.published_snapshot
                )
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            ).fetchall()
            stops = connection.execute(
                """
                WITH latest_snapshot AS (
                    SELECT snapshot_date FROM marts.published_snapshot
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
        if request.path == "/api/refresh":
            refresh_payload = json.dumps(refresh_status(), default=_json_default)
            self._send(HTTPStatus.OK, refresh_payload, "application/json")
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

    def do_POST(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/api/refresh/cancel":
            if self.headers.get("X-Mobility-Action") != "cancel":
                self._send(
                    HTTPStatus.FORBIDDEN,
                    b'{"error":"cancel header required"}',
                    "application/json",
                )
                return
            cancelled, state = cancel_refresh()
            payload = json.dumps(state, default=_json_default)
            self._send(
                HTTPStatus.ACCEPTED if cancelled else HTTPStatus.CONFLICT,
                payload,
                "application/json",
            )
            return
        if request.path != "/api/refresh":
            self._send(HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json")
            return
        if self.headers.get("X-Mobility-Action") != "refresh":
            self._send(
                HTTPStatus.FORBIDDEN, b'{"error":"refresh header required"}', "application/json"
            )
            return
        started, state = start_refresh()
        payload = json.dumps(state, default=_json_default)
        self._send(
            HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT, payload, "application/json"
        )

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
