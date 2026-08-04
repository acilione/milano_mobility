from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from milano_mobility import web


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
