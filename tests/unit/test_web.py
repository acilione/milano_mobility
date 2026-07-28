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
    assert "Milano Mobility Observatory" in web.DASHBOARD_HTML
    assert "Departures by service hour" in web.DASHBOARD_HTML
    assert "Stop constellation" in web.DASHBOARD_HTML
    assert "/api/dashboard" in web.DASHBOARD_HTML
