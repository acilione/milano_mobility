from __future__ import annotations

from typing import Any

import pytest

from milano_mobility.commute import Connection, reachable_stops, walking_seconds
from milano_mobility.web import _commute_parameters


def stops() -> list[dict[str, Any]]:
    return [
        dict(stop_id=name, stop_name=name, stop_lat=45.46, stop_lon=9.0 + i * 0.04)
        for i, name in enumerate("ABCDE")
    ]


def route(
    connections: list[Connection], destination: str = "C", deadline: int = 3600, budget: int = 1800
) -> dict[str, dict[str, Any]]:
    places = stops()
    target = next(s for s in places if s["stop_id"] == destination)
    return {
        s["stop_id"]: s
        for s in reachable_stops(
            places,
            connections,
            (target["stop_lon"], target["stop_lat"]),
            deadline,
            budget,
            300,
        )
    }


def test_walk_only_and_out_of_range() -> None:
    result = route([])
    assert set(result) == {"C"}
    assert result["C"]["minutes"] == 0
    assert result["C"]["legs"][0]["mode"] == "walk"
    assert walking_seconds((9, 45), (9, 45)) == 0
    assert 110 <= walking_seconds((9, 45), (9.001, 45.001)) <= 160


def test_through_ride_needs_no_transfer_allowance() -> None:
    result = route(
        [
            Connection("one", "A", "B", 2400, 3000, "M1", 1),
            Connection("one", "B", "C", 3000, 3500, "M1", 2),
        ]
    )
    assert result["A"]["departure"] == 2400
    assert len(result["A"]["legs"]) == 2
    assert result["A"]["legs"][0]["from"] == "A"
    assert result["A"]["legs"][0]["to"] == "C"
    assert result["A"]["legs"][0]["arrival"] == 3500


@pytest.mark.parametrize(("arrival", "reachable"), [(2880, True), (2881, False), (3000, False)])
def test_transfer_requires_two_minutes(arrival: int, reachable: bool) -> None:
    result = route(
        [
            Connection("one", "A", "B", 2400, arrival, "1"),
            Connection("two", "B", "C", 3000, 3500, "2"),
        ]
    )
    assert ("A" in result) is reachable


def test_four_boardings_are_excluded() -> None:
    result = route(
        [
            Connection("one", "A", "B", 2000, 2200, "1"),
            Connection("two", "B", "C", 2400, 2600, "2"),
            Connection("three", "C", "D", 2800, 3000, "3"),
            Connection("four", "D", "E", 3200, 3500, "4"),
        ],
        destination="E",
    )
    assert "B" in result
    assert "A" not in result
    assert len([leg for leg in result["B"]["legs"] if leg["mode"] == "transit"]) == 3


def test_walking_transfer_and_final_walk() -> None:
    places = stops()
    places[2]["stop_lon"] = places[1]["stop_lon"] + 0.001
    result = reachable_stops(
        places,
        [
            Connection("one", "A", "B", 2000, 2400, "1"),
            Connection("two", "C", "D", 3000, 3400, "2"),
        ],
        (places[3]["stop_lon"] + 0.001, 45.46),
        3600,
        1800,
        300,
    )
    origin = next(s for s in result if s["stop_id"] == "A")
    assert [leg["mode"] for leg in origin["legs"]] == ["transit", "walk", "transit", "walk"]
    assert origin["legs"][1]["from"] == "B"
    assert origin["legs"][1]["to"] == "C"


def test_restricted_boarding_and_alighting_allow_through_ride() -> None:
    result = route(
        [
            Connection("one", "A", "B", 2400, 3000, "M1", 1, can_alight=False),
            Connection("one", "B", "C", 3000, 3500, "M1", 2, can_board=False),
        ]
    )
    assert "A" in result
    assert "B" not in result
    assert "A" not in route([Connection("one", "A", "C", 2400, 3500, "1", can_alight=False)])


def test_budget_deadline_and_latest_departure() -> None:
    result = route(
        [
            Connection("early", "A", "C", 1800, 3300, "1"),
            Connection("better", "A", "C", 2400, 3500, "2"),
            Connection("late", "B", "C", 3000, 3601, "3"),
            Connection("outside", "D", "C", 1799, 3500, "4"),
            Connection("unknown", "missing", "C", 3000, 3500, "5"),
        ]
    )
    assert result["A"]["departure"] == 2400
    assert "B" not in result and "D" not in result


def test_previous_day_connections_keep_negative_departure() -> None:
    result = route([Connection("yesterday:one", "A", "C", -300, 600, "N1")], deadline=900)
    assert result["A"]["departure"] == -300
    assert result["A"]["minutes"] == 20


@pytest.mark.parametrize(
    "change",
    [
        "lat=nan",
        "lon=inf",
        "lat=0",
        "minutes=1000",
        "walk=0",
        "time=25:00",
        "date=2026-02-30",
        "minutes=abc",
        "time=09:00:00",
    ],
)
def test_invalid_requests_are_rejected(change: str) -> None:
    fields = dict(lat="45.46", lon="9.19", date="2026-09-07", time="09:00", minutes="30", walk="10")
    key, value = change.split("=")
    fields[key] = value
    with pytest.raises(ValueError):
        _commute_parameters("&".join(f"{k}={v}" for k, v in fields.items()))


def test_valid_request_and_missing_parameters() -> None:
    result = _commute_parameters("lat=45.46&lon=9.19&date=2026-09-07&time=09:30&minutes=45&walk=10")
    assert result[1:4] == (34200, 2700, 600)
    with pytest.raises(ValueError):
        _commute_parameters("")
