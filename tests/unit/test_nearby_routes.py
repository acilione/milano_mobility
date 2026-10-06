from __future__ import annotations

import io
import json
from typing import Any

import pytest
import requests

from milano_mobility import nearby, route_maps, web


def selection() -> dict[str, Any]:
    return {
        "addresses": [{"name": "Home", "point": [9.2, 45.46]}],
        "categories": ["cafe", "cinema"],
        "radius": 1000,
        "minutes": 10,
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"addresses": []},
        {"categories": []},
        {"categories": ["bogus"]},
        {"radius": 10000},
        {"minutes": 11},
        {"addresses": [{"name": "X", "point": [0, 0]}]},
    ],
)
def test_nearby_rejects_invalid_selection(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        nearby.compare_nearby(dict(selection(), **changes))


def test_nearby_shortlist_counts_unknown_routes_and_empty_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    places = [
        {
            "id": f"node/{i}",
            "name": f"Cafe {i}",
            "point": [9.2 + i * 0.00001, 45.46],
            "categories": ["cafe"],
        }
        for i in range(24)
    ]
    monkeypatch.setattr(
        nearby,
        "_places",
        lambda *args: {"places": places, "retrieved_at": "now", "osm_timestamp": None},
    )
    monkeypatch.setattr(nearby, "walking_times", lambda *args: [None, 601, *([120] * 18)])
    result = nearby.compare_nearby(selection())["addresses"][0]
    cafe = result["categories"]["cafe"]
    assert cafe["reachable_count"] == 18 and cafe["mapped_count"] == 24
    assert cafe["sampled"] and cafe["checked_count"] == 20 and cafe["unknown_count"] == 1
    assert cafe["nearest_seconds"] == 120
    assert result["categories"]["cinema"]["nearest_seconds"] is None
    assert result["categories"]["cinema"]["reachable_count"] == 0


def test_unavailable_address_is_not_a_zero_count(monkeypatch: pytest.MonkeyPatch) -> None:
    def failed(*args: Any) -> Any:
        raise requests.Timeout()

    monkeypatch.setattr(nearby, "_places", failed)
    result = nearby.compare_nearby(selection())["addresses"][0]
    assert result["status"] == "unavailable" and "categories" not in result


def test_osm_discovery_filters_duplicates_private_and_remote_places(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nearby._places.cache_clear()
    node = {
        "type": "node",
        "id": 1,
        "lat": 45.46,
        "lon": 9.2,
        "tags": {
            "amenity": "cafe",
            "name": "Cafe",
            "addr:street": "Via Roma",
            "opening_hours": "Mo-Fr 09:00-18:00",
        },
    }
    payload = {
        "elements": [
            node,
            node,
            dict(node, id=2, tags={"amenity": "cafe", "access": "private"}),
            dict(node, id=3, lon=9.5),
            {},
            dict(node, id=4, tags={"amenity": "cinema"}),
        ],
        "osm3s": {"timestamp_osm_base": "today"},
    }

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return payload

    monkeypatch.setattr(nearby.requests, "post", lambda *args, **kwargs: Response())
    result = nearby._places((9.2, 45.46), 1000, "today")
    assert len(result["places"]) == 2 and result["osm_timestamp"] == "today"
    assert result["places"][0]["osm_url"] == "https://www.openstreetmap.org/node/1"
    nearby._places.cache_clear()
    payload["remark"] = "runtime error"
    with pytest.raises(requests.RequestException):
        nearby._places((9.2, 45.46), 1000, "today")
    payload.pop("remark")
    payload["elements"] = [node] * 5001
    with pytest.raises(ValueError):
        nearby._places((9.2, 45.46), 1000, "today")
    nearby._places.cache_clear()


def test_bookshops_and_libraries_have_separate_discovery_and_walking_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nearby._places.cache_clear()
    node = {"type": "node", "lat": 45.46, "lon": 9.2}

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> Any:
            return {
                "elements": [
                    dict(node, id=1, tags={"shop": "books", "name": "Bookshop"}),
                    dict(node, id=2, tags={"amenity": "library", "name": "Library"}),
                    dict(node, id=3, tags={"shop": "stationery"}),
                    dict(node, id=4, tags={"amenity": "library", "access": "private"}),
                ]
            }

    def discover(*args: Any, **kwargs: Any) -> Response:
        query = kwargs["data"]["data"]
        assert '["shop"~"^(books)$"]' in query
        assert '["amenity"~"^(library)$"]' in query
        return Response()

    monkeypatch.setattr(nearby.requests, "post", discover)
    monkeypatch.setattr(nearby, "walking_times", lambda origin, destinations: [120, 720])
    try:
        result = nearby.compare_nearby(dict(selection(), categories=["bookshop", "library"]))
        groups = result["addresses"][0]["categories"]
        assert groups["bookshop"]["mapped_count"] == 1
        assert groups["bookshop"]["reachable_count"] == 1
        assert groups["bookshop"]["places"][0]["name"] == "Bookshop"
        assert groups["library"]["mapped_count"] == 1
        assert groups["library"]["reachable_count"] == 0
        assert groups["library"]["nearest_seconds"] == 720
    finally:
        nearby._places.cache_clear()


def test_walking_table_keeps_unknown_and_rejects_bad_data(monkeypatch: pytest.MonkeyPatch) -> None:
    nearby.walking_times.cache_clear()
    payload = {
        "code": "Ok",
        "durations": [[100, None, 20]],
        "sources": [{"distance": 10}],
        "destinations": [{"distance": 0}, {"distance": 0}, {"distance": 101}],
    }
    monkeypatch.setattr(nearby, "_request", lambda *args: payload)
    points = ((9.21, 45.46), (9.22, 45.46), (9.23, 45.46))
    assert nearby.walking_times((9.2, 45.46), points) == [108, None, None]
    nearby.walking_times.cache_clear()
    payload["durations"] = [[float("nan"), 0, 0]]
    with pytest.raises(requests.RequestException):
        nearby.walking_times((9.2, 45.46), points)
    with pytest.raises(ValueError):
        nearby.walking_times((9.2, 45.46), ())
    nearby.walking_times.cache_clear()


def route_payload() -> dict[str, Any]:
    return {
        "code": "Ok",
        "waypoints": [{"distance": 5}, {"distance": 5}],
        "routes": [
            {
                "duration": 100,
                "distance": 120,
                "geometry": {"coordinates": [[9.2, 45.46], [9.21, 45.46]]},
                "legs": [
                    {
                        "steps": [
                            {
                                "name": "Via Roma",
                                "distance": 120,
                                "maneuver": {"type": "turn", "modifier": "left"},
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_walking_route_geometry_instructions_and_invalid_snapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_maps.walking_route.cache_clear()
    payload = route_payload()
    monkeypatch.setattr(route_maps, "_request", lambda *args: payload)
    result = route_maps.walking_route((9.2, 45.46), (9.21, 45.46))
    assert result["seconds"] == 108 and result["metres"] == 130
    assert result["steps"][0]["instruction"] == "Turn left — Via Roma"
    assert len(result["access"]) == 2
    route_maps.walking_route.cache_clear()
    payload["waypoints"][0]["distance"] = 101
    with pytest.raises(requests.RequestException):
        route_maps.walking_route((9.2, 45.46), (9.21, 45.46))
    route_maps.walking_route.cache_clear()


def test_route_map_official_shape_and_explicit_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    points = [(9.2, 45.46), (9.21, 45.46), (9.22, 45.46)]
    assert route_maps.clip_shape(points, [points[0], points[-1]]) == points
    assert route_maps.clip_shape(points, [(9.8, 45.46), (9.9, 45.46)]) is None
    assert route_maps.clip_shape([], points) is None
    assert route_maps.clip_shape(points, [points[-1], points[0]]) is None
    legs = [{"mode": "transit", "route": "1", "trip_id": "trip", "coordinates": points}]
    result = route_maps.journey_map({"legs": legs}, lambda trip: points)
    assert result["features"][0]["properties"]["kind"] == "official_transit"
    result = route_maps.journey_map({"legs": legs}, lambda trip: [])
    assert result["notes"] and result["features"][0]["properties"]["kind"] == "scheduled_stops"

    def failed(*args: Any) -> Any:
        raise requests.Timeout()

    monkeypatch.setattr(route_maps, "walking_route", failed)
    result = route_maps.journey_map({"legs": [dict(legs[0], mode="walk")]}, lambda trip: [])
    assert result["features"][0]["properties"]["kind"] == "unverified_walk" and result["notes"]
    monkeypatch.setattr(
        route_maps,
        "walking_route",
        lambda *args: {
            "coordinates": points,
            "access": [[points[0], points[0]], [points[-1], points[-1]]],
        },
    )
    result = route_maps.journey_map({"legs": [dict(legs[0], mode="walk")]}, lambda trip: [])
    assert result["features"][-1]["properties"]["kind"] == "street_walk"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"legs": []},
        {"legs": [{"mode": "car", "coordinates": []}]},
        {"legs": [{"mode": "walk", "coordinates": [[0, 0], [9.2, 45.46]]}]},
    ],
)
def test_route_map_validation(payload: Any) -> None:
    with pytest.raises(ValueError):
        route_maps.journey_map(payload, lambda trip: [])


@pytest.mark.parametrize("path", ["nearby", "walking-route", "journey-map"])
def test_exploration_http_endpoints(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    handler = object.__new__(web.DashboardHandler)
    sent: list[Any] = []
    body = json.dumps(dict(selection(), origin=[9.2, 45.46], destination=[9.21, 45.46])).encode()
    monkeypatch.setattr(handler, "path", f"/api/{path}", raising=False)
    monkeypatch.setattr(
        handler,
        "headers",
        {"Content-Length": str(len(body)), "X-Mobility-Action": "explore"},
        raising=False,
    )
    monkeypatch.setattr(handler, "rfile", io.BytesIO(body), raising=False)
    monkeypatch.setattr(handler, "_send", lambda *args: sent.append(args))
    monkeypatch.setattr(web, "compare_nearby", lambda *args: {"addresses": []})
    monkeypatch.setattr(web, "walking_route", lambda *args: {"coordinates": []})
    monkeypatch.setattr(web, "load_journey_map", lambda *args: {"features": []})
    handler.do_POST()
    assert sent[-1][0] == 200
    monkeypatch.setattr(handler, "headers", {"Content-Length": "70000"}, raising=False)
    handler.do_POST()
    assert sent[-1][0] == 400


def test_comparison_return_geometry_uses_correct_direction() -> None:
    from milano_mobility.commute import Connection
    from milano_mobility.comparison import compare_day, validate_comparison

    payload = {
        "destination": {"point": [9.22, 45.46]},
        "addresses": [{"name": "Home", "point": [9.19, 45.46]}],
        "week": "2026-10-05",
        "days": [0],
        "arrival": "09:00",
        "departure": "18:00",
        "walk": 10,
    }
    settings = validate_comparison(payload)
    stops = [
        {"stop_id": "A", "stop_name": "A", "stop_lon": 9.2, "stop_lat": 45.46},
        {"stop_id": "B", "stop_name": "B", "stop_lon": 9.21, "stop_lat": 45.46},
    ]
    morning = [Connection("2026-10-05:out", "A", "B", 31800, 32100, "1")]
    evening = [Connection("2026-10-05:back", "B", "A", 64920, 65220, "1")]
    result = compare_day(
        stops,
        morning,
        evening,
        settings,
        [{"B": 60}, {"A": 60}],
        [{"B": 60}, {"A": 60}],
        [[0, None], [None, 0]],
    )[0]
    assert result["outbound"]["legs"][0]["coordinates"][0] == (9.19, 45.46)
    inbound = result["return"]["legs"]
    assert inbound[0]["coordinates"][0] == (9.22, 45.46)
    assert inbound[-1]["coordinates"][-1] == (9.19, 45.46)
    transit = next(leg for leg in inbound if leg["mode"] == "transit")
    assert transit["coordinates"] == [(9.21, 45.46), (9.2, 45.46)]
    assert transit["trip_id"] == "back"
