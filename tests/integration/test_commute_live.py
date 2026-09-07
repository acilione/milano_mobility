from __future__ import annotations

import os
from datetime import date
from urllib.parse import urlencode

import pytest

from milano_mobility import web


@pytest.mark.integration
def test_commute_reads_published_timetable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise BI grants, calendar joins and routing without modifying the feed."""
    monkeypatch.setenv("POSTGRES_HOST", os.getenv("TEST_POSTGRES_HOST", "localhost"))
    data = web.load_dashboard_data()
    assert data["state"] == "ready"
    days = [str(row["service_date"]) for row in data["service_days"]]
    day = date.today().isoformat() if date.today().isoformat() in days else days[0]
    query = urlencode(dict(lat=45.4642, lon=9.19, date=day, time="09:00", minutes=30, walk=10))
    result = web.load_commute(query)
    assert result["connections"] > 0
    assert result["stops"]
    assert any(leg["mode"] == "transit" for s in result["stops"] for leg in s["legs"])
    for stop in result["stops"]:
        assert 0 <= stop["minutes"] <= 30
        assert sum(leg["mode"] == "transit" for leg in stop["legs"]) <= 3
        for leg in stop["legs"]:
            assert leg["departure"] <= leg["arrival"] <= result["deadline"]
        for first, second in zip(stop["legs"], stop["legs"][1:], strict=False):
            assert first["arrival"] <= second["departure"]
