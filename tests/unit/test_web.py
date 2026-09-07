from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest

from milano_mobility import web
from milano_mobility.models import ManifestStatus


def refresh_state() -> dict[str, object]:
    return {
        "state": "idle",
        "stage": "idle",
        "progress": 0,
        "message": "Ready",
        "pipeline_run_id": None,
        "snapshot_date": None,
        "started_at": None,
        "completed_at": None,
    }


def test_dashboard_uses_bi_reader_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BI_DB_USER", "visual_reader")
    monkeypatch.setenv("BI_DB_PASSWORD", "visual_secret")

    parameters = web._database_parameters()

    assert parameters["user"] == "visual_reader"
    assert parameters["password"] == "visual_secret"


def test_dashboard_metadata_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_TITLE", "City Network")
    monkeypatch.setenv("SERVICE_AREA_NAME", "Example City")
    monkeypatch.setenv("DASHBOARD_REFRESH_SECONDS", "45")

    metadata = web._dashboard_metadata()

    assert metadata["title"] == "City Network"
    assert metadata["area_name"] == "Example City"
    assert metadata["refresh_seconds"] == 45


def test_route_shape_ids_are_deduplicated_validated_and_bounded() -> None:
    valid = "a" * 32

    assert web._normalize_route_ids([valid, "not-a-key", valid]) == [valid]
    assert len(web._normalize_route_ids([f"{value:032x}" for value in range(60)])) == 50


def test_route_shapes_skip_database_when_no_valid_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_connection(**_: object) -> None:
        raise AssertionError("database should not be called")

    monkeypatch.setattr(web.psycopg, "connect", unexpected_connection)

    assert web.load_route_shapes(["invalid"]) == []


def test_route_shapes_return_empty_when_mart_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_database(**_: object) -> None:
        raise psycopg.OperationalError("route shape mart is preparing")

    monkeypatch.setattr(web.psycopg, "connect", unavailable_database)

    assert web.load_route_shapes(["a" * 32]) == []


def test_route_shapes_return_sampled_geometry(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = [{"route_sk": "a" * 32, "shape_id": "shape-1", "points": [[9.1, 45.4]]}]

    class Result:
        def fetchall(self) -> list[dict[str, object]]:
            return expected

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def execute(self, query: str, parameters: tuple[list[str]]) -> Result:
            assert "marts.dashboard_route_shape" in query
            assert parameters == (["a" * 32],)
            return Result()

    monkeypatch.setattr(web.psycopg, "connect", lambda **_: Connection())

    assert web.load_route_shapes(["a" * 32]) == expected


def test_json_default_serializes_warehouse_values() -> None:
    assert web._json_default(date(2026, 7, 28)) == "2026-07-28"
    assert web._json_default(Decimal("12.50")) == "12.50"


def test_json_default_rejects_unknown_values() -> None:
    with pytest.raises(TypeError, match="Cannot serialize"):
        web._json_default(object())


def test_static_assets_are_allow_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asset = tmp_path / "dist" / "map.js"
    asset.parent.mkdir()
    asset.write_bytes(b"map bundle")
    monkeypatch.setattr(web, "STATIC_ROOT", tmp_path)

    assert web._static_asset("/assets/map.js") == (b"map bundle", "text/javascript; charset=utf-8")
    assert web._static_asset("/assets/maplibre-gl.css") is None
    assert web._static_asset("/assets/unknown.js") is None


def test_dashboard_waits_cleanly_before_marts_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_database(**_: object) -> None:
        raise psycopg.OperationalError("database is preparing")

    monkeypatch.setattr(web.psycopg, "connect", unavailable_database)

    payload = web.load_dashboard_data()

    assert payload["state"] == "waiting"
    assert "first published snapshot" in payload["message"]


def test_dashboard_shell_contains_primary_visuals() -> None:
    assert "Departures by service hour" in web.DASHBOARD_HTML
    assert "Interactive stop map" in web.DASHBOARD_HTML
    assert 'import {createMap} from "/assets/map.js"' in web.DASHBOARD_HTML
    assert 'href="/assets/maplibre-gl.css"' in web.DASHBOARD_HTML
    assert 'createMap("network-map"' in web.DASHBOARD_HTML
    assert "connected routes" in web.DASHBOARD_HTML
    assert "/api/dashboard" in web.DASHBOARD_HTML
    assert 'id="refresh-data"' in web.DASHBOARD_HTML
    assert 'id="refresh-bar"' in web.DASHBOARD_HTML
    assert "/api/refresh/cancel" in web.DASHBOARD_HTML
    assert "Cancel update" in web.DASHBOARD_HTML
    assert "trend-tooltip" in web.DASHBOARD_HTML
    assert "trend-axis" in web.DASHBOARD_HTML
    assert "From source to evidence" not in web.DASHBOARD_HTML
    assert "Version-aware" not in web.DASHBOARD_HTML


def test_refresh_status_is_an_isolated_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_REFRESH_STATE", refresh_state())

    status = web.refresh_status()
    status["state"] = "changed outside"

    assert web.refresh_status()["state"] == "idle"


@pytest.mark.parametrize(
    ("pipeline_status", "expected_state"),
    [
        (ManifestStatus.PUBLISHED, "succeeded"),
        (ManifestStatus.SKIPPED, "unchanged"),
    ],
)
def test_background_refresh_publishes_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
    pipeline_status: ManifestStatus,
    expected_state: str,
) -> None:
    monkeypatch.setattr(web, "_REFRESH_STATE", refresh_state())
    monkeypatch.setattr(
        web,
        "Settings",
        lambda: SimpleNamespace(
            source_url="https://example.test/latest.zip", service_timezone="UTC"
        ),
    )

    def run_pipeline(*args: object, **kwargs: object) -> SimpleNamespace:
        assert args[0] == "https://example.test/latest.zip"
        assert kwargs["build_warehouse"] is True
        kwargs["progress_callback"]("modeling", 80, "Building models")
        return SimpleNamespace(status=pipeline_status)

    monkeypatch.setattr(web, "_execute_pipeline", run_pipeline)

    web._run_refresh("run-1")

    status = web.refresh_status()
    assert status["state"] == expected_state
    assert status["progress"] == 100
    assert status["completed_at"] is not None


def test_background_refresh_reports_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "_REFRESH_STATE", refresh_state())
    monkeypatch.setattr(
        web,
        "Settings",
        lambda: SimpleNamespace(source_url="", service_timezone="UTC"),
    )

    web._run_refresh("run-2")

    status = web.refresh_status()
    assert status["state"] == "failed"
    assert status["stage"] == "failed"
    assert "GTFS_SOURCE_URL" in str(status["message"])


def test_background_refresh_reports_cancellation(monkeypatch: pytest.MonkeyPatch) -> None:
    cancellation = web.Event()
    cancellation.set()
    monkeypatch.setattr(web, "_REFRESH_STATE", refresh_state())
    monkeypatch.setattr(web, "_REFRESH_CANCEL", cancellation)
    monkeypatch.setattr(
        web,
        "Settings",
        lambda: SimpleNamespace(
            source_url="https://example.test/latest.zip", service_timezone="UTC"
        ),
    )

    def run_pipeline(*_: object, **kwargs: object) -> SimpleNamespace:
        kwargs["progress_callback"]("downloading", 12, "Downloading")
        raise AssertionError("the cancellation callback should stop the pipeline")

    monkeypatch.setattr(web, "_execute_pipeline", run_pipeline)

    web._run_refresh("run-cancelled")

    status = web.refresh_status()
    assert status["state"] == "cancelled"
    assert "published snapshot was not changed" in str(status["message"])


def test_start_refresh_rejects_concurrent_run(monkeypatch: pytest.MonkeyPatch) -> None:
    state = refresh_state()
    state["state"] = "running"
    monkeypatch.setattr(web, "_REFRESH_STATE", state)

    started, returned_state = web.start_refresh()

    assert started is False
    assert returned_state["state"] == "running"


def test_start_refresh_launches_background_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    state = refresh_state()
    started_threads: list[tuple[object, tuple[object, ...]]] = []
    monkeypatch.setattr(web, "_REFRESH_STATE", state)
    monkeypatch.setattr(
        web,
        "Settings",
        lambda: SimpleNamespace(
            source_url="https://example.test/latest.zip", service_timezone="UTC"
        ),
    )

    class FakeThread:
        def __init__(self, *, target: object, args: tuple[object, ...], **_: object) -> None:
            self.target = target
            self.args = args

        def start(self) -> None:
            started_threads.append((self.target, self.args))

    monkeypatch.setattr(web, "Thread", FakeThread)

    started, returned_state = web.start_refresh()

    assert started is True
    assert returned_state["state"] == "running"
    assert returned_state["progress"] == 1
    assert started_threads[0][0] is web._run_refresh


def test_cancel_refresh_signals_active_download(monkeypatch: pytest.MonkeyPatch) -> None:
    state = refresh_state()
    state.update(state="running", stage="downloading", progress=15)
    cancellation = web.Event()
    monkeypatch.setattr(web, "_REFRESH_STATE", state)
    monkeypatch.setattr(web, "_REFRESH_CANCEL", cancellation)

    cancelled, returned_state = web.cancel_refresh()

    assert cancelled is True
    assert cancellation.is_set()
    assert returned_state["stage"] == "cancelling"


def test_cancel_refresh_rejects_non_download_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    state = refresh_state()
    state.update(state="running", stage="modeling", progress=80)
    monkeypatch.setattr(web, "_REFRESH_STATE", state)

    cancelled, returned_state = web.cancel_refresh()

    assert cancelled is False
    assert returned_state["stage"] == "modeling"


def test_refresh_status_endpoint_returns_json(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = object.__new__(web.DashboardHandler)
    sent: list[tuple[object, object, str]] = []
    monkeypatch.setattr(handler, "path", "/api/refresh", raising=False)
    monkeypatch.setattr(
        handler,
        "_send",
        lambda status, body, content_type: sent.append((status, body, content_type)),
    )
    monkeypatch.setattr(web, "_REFRESH_STATE", refresh_state())

    handler.do_GET()

    assert sent[0][0] == 200
    assert '"state": "idle"' in str(sent[0][1])
    assert sent[0][2] == "application/json"


@pytest.mark.parametrize(
    ("error", "status"),
    [(None, 200), (ValueError("Invalid date"), 400), (psycopg.OperationalError("offline"), 503)],
)
def test_commute_http_responses(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, status: int
) -> None:
    handler = object.__new__(web.DashboardHandler)
    sent: list[tuple[object, object, str]] = []
    monkeypatch.setattr(handler, "path", "/api/commute?date=2026-09-07", raising=False)
    monkeypatch.setattr(handler, "_send", lambda *args: sent.append(args))

    def load(query: str) -> dict[str, object]:
        assert query == "date=2026-09-07"
        if error:
            raise error
        return {"stops": []}

    monkeypatch.setattr(web, "load_commute", load)
    handler.do_GET()
    assert sent[0][0] == status
    assert isinstance(json.loads(str(sent[0][1])), dict)


def test_commute_timetable_uses_calendar_offsets_and_run_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web._commute_timetable.cache_clear()
    queries: list[str] = []

    class Result:
        def __init__(self, query: str) -> None:
            self.query = query

        def fetchone(self) -> dict[str, object]:
            if "min(service_date)" in self.query:
                return {"first": date(2026, 9, 1), "last": date(2026, 9, 30)}
            return {"pipeline_run_id": "run"}

        def fetchall(self) -> list[dict[str, object]]:
            if "commute_stops" in self.query:
                return []
            return [
                dict(
                    service_date=date(2026, 9, 6),
                    trip_id="night",
                    from_stop="A",
                    to_stop="B",
                    departure=-300,
                    arrival=600,
                    route_name="N1",
                    stop_sequence=1,
                    can_board=True,
                    can_alight=False,
                )
            ]

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *_: object) -> None:
            pass

        def execute(self, query: str, parameters: object = None) -> Result:
            queries.append(query)
            if "SELECT c.*" in query:
                assert parameters == dict(day=date(2026, 9, 7), run="run", start=-900, end=900)
            return Result(query)

    monkeypatch.setattr(web.psycopg, "connect", lambda **_: Connection())
    _, connections = web._commute_timetable("run", date(2026, 9, 7), 900, 1800)
    assert connections[0].trip == "2026-09-06:night"
    assert connections[0].departure == -300
    assert not connections[0].can_alight
    assert any("s.service_date - %(day)s::date" in query for query in queries)
    with pytest.raises(ValueError, match="changed"):
        web._commute_timetable("different", date(2026, 9, 7), 900, 1800)
    with pytest.raises(ValueError, match="outside"):
        web._commute_timetable("run", date(2026, 10, 1), 900, 1800)
    web._commute_timetable.cache_clear()
