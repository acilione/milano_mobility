from __future__ import annotations

from datetime import date
from decimal import Decimal

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


def test_json_default_serializes_warehouse_values() -> None:
    assert web._json_default(date(2026, 7, 28)) == "2026-07-28"
    assert web._json_default(Decimal("12.50")) == "12.50"


def test_json_default_rejects_unknown_values() -> None:
    with pytest.raises(TypeError, match="Cannot serialize"):
        web._json_default(object())


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
    assert 'data-map-action="in"' in web.DASHBOARD_HTML
    assert "connected routes" in web.DASHBOARD_HTML
    assert "/api/dashboard" in web.DASHBOARD_HTML
