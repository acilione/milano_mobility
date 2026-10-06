from __future__ import annotations

import copy
import io
import json
from datetime import date
from typing import Any

import pytest
import requests

from milano_mobility import comparison, places, web
from milano_mobility.commute import Connection, reachable_stops


@pytest.mark.parametrize("response", [None, [], {"features": None}])
def test_geocoder_malformed_response(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    places.search_places.cache_clear()
    monkeypatch.setattr(places, "_request", lambda *args: response)
    with pytest.raises(requests.RequestException):
        places.search_places("Via Roma Milano")


@pytest.mark.parametrize(
    "body,size,header,status",
    [
        (b"{}", "2", "compare", 200),
        (b"{", "1", "compare", 400),
        (b"{}", "2", "", 400),
        (b"{}", "16001", "compare", 400),
        (b"{}", "invalid", "compare", 400),
    ],
)
def test_comparison_request_validation(
    monkeypatch: pytest.MonkeyPatch, body: bytes, size: str, header: str, status: int
) -> None:
    handler = object.__new__(web.DashboardHandler)
    monkeypatch.setattr(handler, "path", "/api/comparison", raising=False)
    monkeypatch.setattr(
        handler, "headers", {"Content-Length": size, "X-Mobility-Action": header}, raising=False
    )
    monkeypatch.setattr(handler, "rfile", io.BytesIO(body), raising=False)
    sent: list[Any] = []
    monkeypatch.setattr(handler, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(web, "load_comparison", lambda data: {"apartments": []})
    handler.do_POST()
    assert sent[0][0] == status
    assert isinstance(json.loads(sent[0][1]), dict)


def test_comparison_provider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = object.__new__(web.DashboardHandler)
    monkeypatch.setattr(handler, "path", "/api/comparison", raising=False)
    monkeypatch.setattr(
        handler, "headers", {"Content-Length": "2", "X-Mobility-Action": "compare"}, raising=False
    )
    monkeypatch.setattr(handler, "rfile", io.BytesIO(b"{}"), raising=False)
    sent: list[Any] = []
    monkeypatch.setattr(handler, "_send", lambda *args: sent.append(args))

    def failed(data: object) -> None:
        raise requests.Timeout()

    monkeypatch.setattr(web, "load_comparison", failed)
    handler.do_POST()
    assert sent[0][0] == 503
    assert "retry" in json.loads(sent[0][1])["error"]


def payload() -> dict[str, Any]:
    return {
        "destination": {"name": "Work", "point": [9.2, 45.46]},
        "apartments": [{"name": "Apartment", "point": [9.1, 45.46], "rent": 950}],
        "week": "2026-10-07",
        "days": [0, 2, 4],
        "arrival": "09:00",
        "departure": "18:00",
        "walk": 10,
    }


def test_schedule_uses_actual_selected_dates() -> None:
    result = comparison.validate_comparison(payload())
    assert result["dates"] == [date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 9)]
    assert result["arrival"] == 32400
    assert result["departure"] == 64800


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("days", []),
        ("days", [7]),
        ("days", [True]),
        ("week", "invalid"),
        ("week", "2026-02-30"),
        ("arrival", "25:00"),
        ("arrival", "09:60"),
        ("arrival", 9),
        ("departure", "08:00"),
        ("walk", 0),
        ("apartments", []),
        ("apartments", [{}] * 4),
    ],
)
def test_invalid_comparison(key: str, value: Any) -> None:
    data = payload()
    data[key] = value
    with pytest.raises(ValueError):
        comparison.validate_comparison(data)


@pytest.mark.parametrize("rent", [-1, float("nan"), float("inf"), True, "1000"])
def test_invalid_rent(rent: Any) -> None:
    data = payload()
    data["apartments"][0]["rent"] = rent
    with pytest.raises(ValueError):
        comparison.validate_comparison(data)


@pytest.mark.parametrize("point", [None, [], [9], ["x", 45], [float("nan"), 45], [0, 0]])
def test_invalid_point(point: Any) -> None:
    with pytest.raises(ValueError):
        places.valid_point(point)


def test_arrival_margin_is_not_counted_as_travel() -> None:
    reachable = [
        {
            "stop_id": "A",
            "stop_name": "A",
            "departure": 2400,
            "legs": [
                {
                    "mode": "transit",
                    "from": "A",
                    "to": "B",
                    "route": "1",
                    "departure": 2400,
                    "arrival": 3000,
                },
                {
                    "mode": "walk",
                    "from": "B",
                    "to": "Destination",
                    "departure": 3500,
                    "arrival": 3600,
                },
            ],
        }
    ]
    result = comparison.select_journey(reachable, {"A": 120}, None, 3600, 600)
    assert result is not None
    assert result["departure"] == 2280 and result["arrival"] == 3100
    assert result["seconds"] == 820 and result["walking_seconds"] == 220
    assert result["transfers"] == 0


def test_depart_after_respects_waiting_and_directional_access() -> None:
    stops = [
        dict(stop_id="A", stop_name="A", stop_lat=45.46, stop_lon=9.1),
        dict(stop_id="B", stop_name="B", stop_lat=45.46, stop_lon=9.2),
    ]
    original = [Connection("one", "A", "B", 1000, 1300, "1")]
    inverted = comparison.reverse_connections(original)
    reachable = reachable_stops(
        stops, inverted, (9.1, 45.46), -800, 5400, 600, destination_walks={"A": 120}
    )
    result = comparison.select_journey(reachable, {"B": 150}, None, -800, 600, True)
    assert result is not None
    assert result["departure"] == 800 and result["arrival"] == 1450
    assert result["seconds"] == 650
    assert [leg["mode"] for leg in result["legs"]] == ["walk", "transit", "walk"]
    assert result["legs"][1]["from"] == "A" and result["legs"][1]["to"] == "B"
    missed = reachable_stops(
        stops, inverted, (9.1, 45.46), -900, 5400, 600, destination_walks={"A": 120}
    )
    assert comparison.select_journey(missed, {"B": 150}, None, -900, 600, True) is None


def test_direct_walk_and_unavailable_routes() -> None:
    result = comparison.select_journey([], {}, 300, 3600, 600)
    assert result and result["seconds"] == 300 and result["transfers"] == 0
    assert comparison.select_journey([], {}, 601, 3600, 600) is None
    assert (
        comparison.select_journey(
            [dict(stop_id="a", legs=[], departure=0)], {"a": None}, None, 3600, 600
        )
        is None
    )
    walk = [dict(stop_id="a", legs=[{"mode": "walk"}], departure=3500)]
    assert comparison.select_journey(walk, {"a": 100}, None, 3600, 600) is None
    old = [dict(stop_id="a", legs=[{"mode": "transit"}], departure=-3000, stop_name="A")]
    assert comparison.select_journey(old, {"a": 100}, None, 3600, 600) is None


def test_walking_matrix_directions_snapping_and_missing_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places.walking_matrix.cache_clear()
    data = {
        "code": "Ok",
        "durations": [[0, 100], [140, None]],
        "sources": [{"distance": 10}, {"distance": 0}],
        "destinations": [{"distance": 10}, {"distance": 0}],
    }
    monkeypatch.setattr(places, "_request", lambda *args: data)
    matrix = places.walking_matrix(((9.1, 45.46), (9.2, 45.46)))
    assert matrix == [[16, 108], [148, None]]
    places.walking_matrix.cache_clear()
    data["sources"][0]["distance"] = 101
    assert places.walking_matrix(((9.1, 45.46), (9.2, 45.46)))[0] == [None, None]
    places.walking_matrix.cache_clear()
    data["durations"] = [[0]]
    with pytest.raises(ValueError):
        places.walking_matrix(((9.1, 45.46), (9.2, 45.46)))
    with pytest.raises(ValueError):
        places.walking_matrix(())
    places.walking_matrix.cache_clear()


def test_geocoder_filters_invalid_results_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    places.search_places.cache_clear()
    calls = []

    def lookup(*args: Any) -> dict[str, Any]:
        calls.append(args)
        return {
            "features": [
                {
                    "geometry": {"coordinates": [9.2, 45.46]},
                    "properties": {"street": "Via Roma", "housenumber": "10", "city": "Milano"},
                },
                {"geometry": {"coordinates": [0, 0]}, "properties": {}},
                {},
            ]
        }

    monkeypatch.setattr(places, "_request", lookup)
    result = places.search_places("Via Roma 10 Milano")
    assert result[0]["name"] == "Via Roma 10, Milano" and len(result) == 1
    places.search_places("Via Roma 10 Milano")
    assert len(calls) == 1
    with pytest.raises(ValueError):
        places.search_places("x")
    places.search_places.cache_clear()


def test_access_uses_street_matrix_and_limits_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    stops = [dict(stop_id=str(i), stop_lat=45.46, stop_lon=9.1 + i * 0.0001) for i in range(20)]

    def matrix(points: Any) -> list[list[int]]:
        assert len(points) == 13
        return [[i + j for j in range(len(points))] for i in range(len(points))]

    monkeypatch.setattr(comparison, "walking_matrix", matrix)
    access, egress, direct = comparison.access_walks(stops, [(9.1, 45.46)], 600)
    assert len(access[0]) == 12 and egress[0]["0"] == 1 and direct == [[0]]


def test_address_suggestions_filter_municipality_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places.search_places.cache_clear()

    def lookup(url: str, params: dict[str, str], service: str) -> dict[str, Any]:
        assert params["q"] == "Via Pad"
        assert params["bbox"] == "9.04,45.38,9.30,45.54"
        features = [
            {
                "geometry": {"coordinates": [9.2, 45.46]},
                "properties": {"street": "Via Padova", "city": city},
            }
            for city in ["Monza", "Milano", "Milano", "Sesto San Giovanni"]
        ]
        features.extend(
            {
                "geometry": {"coordinates": [9.2, 45.46]},
                "properties": {"street": "Via Padova", "housenumber": str(i), "city": "Milan"},
            }
            for i in range(8)
        )
        return {"features": features}

    monkeypatch.setattr(places, "_request", lookup)
    result = places.search_places("Via Pad")
    assert len(result) == 5
    assert result[0]["name"] == "Via Padova, Milano"
    assert all("Monza" not in row["name"] and "Sesto" not in row["name"] for row in result)
    places.search_places.cache_clear()


def test_weekly_sum_withholds_incomplete_week(monkeypatch: pytest.MonkeyPatch) -> None:
    class Cursor:
        def __init__(self, query: str) -> None:
            self.query = query

        def fetchone(self) -> dict[str, Any]:
            if "min(service_date)" in self.query:
                return {"first": date(2026, 10, 5), "last": date(2026, 10, 11)}
            return {"pipeline_run_id": "run", "snapshot_date": date(2026, 10, 5)}

    class DB:
        def __enter__(self) -> DB:
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def execute(self, query: str, *args: Any) -> Cursor:
            return Cursor(query)

    monkeypatch.setattr(web.psycopg, "connect", lambda **kwargs: DB())
    monkeypatch.setattr(web, "_commute_timetable", lambda *args: ([], []))
    monkeypatch.setattr(web, "access_walks", lambda *args: ([], [], []))
    row = {"outbound": {"seconds": 1000}, "return": {"seconds": 2000}, "return_later": None}
    monkeypatch.setattr(web, "compare_day", lambda *args: [copy.deepcopy(row)])
    result = web.load_comparison(payload())
    assert result["apartments"][0]["weekly_seconds"] == 9000
    row["return"] = None
    assert web.load_comparison(payload())["apartments"][0]["weekly_seconds"] is None
    missing = payload()
    missing["week"] = "2026-10-12"
    result = web.load_comparison(missing)
    assert result["apartments"][0]["weekly_seconds"] is None
    assert result["apartments"][0]["days"][0]["status"] == "unavailable"
    edge = payload()
    edge["days"] = [6]
    edge["departure"] = "23:30"
    assert "beyond" in web.load_comparison(edge)["apartments"][0]["days"][0]["reason"]


def test_compare_day_keeps_return_later_separate() -> None:
    settings = comparison.validate_comparison(payload())
    results = comparison.compare_day([], [], [], settings, [{}, {}], [{}, {}], [[0, 300], [400, 0]])
    assert results[0]["outbound"]["seconds"] == 400
    assert results[0]["return"]["seconds"] == 300
    assert results[0]["return_later"]["arrival"] - results[0]["return"]["arrival"] == 600
