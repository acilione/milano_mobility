from __future__ import annotations

import pytest

from milano_mobility.config import Settings


def test_settings_read_environment_when_instantiated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERVICE_TIMEZONE", "Europe/London")
    monkeypatch.setenv("GTFS_SOURCE_NAME", "runtime-feed")

    settings = Settings()

    assert settings.service_timezone == "Europe/London"
    assert settings.source_name == "runtime-feed"
