"""Apartment comparison over an explicit week and office-day selection."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from milano_mobility.commute import Connection, reachable_stops, walking_seconds
from milano_mobility.places import valid_point, walking_matrix


def validate_comparison(payload: Any) -> dict[str, Any]:
    try:
        if not isinstance(payload, dict):
            raise ValueError
        destination = valid_point(payload["destination"]["point"])
        day = date.fromisoformat(payload["week"])
        day -= timedelta(days=day.weekday())
        weekdays = sorted(set(payload["days"]))
        if not weekdays or any(type(v) is not int or not 0 <= v <= 6 for v in weekdays):
            raise ValueError
        times = []
        for key in ("arrival", "departure"):
            value = payload[key]
            if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
                raise ValueError
            hour, minute = int(value[:2]), int(value[3:])
            if not (0 <= hour < 24 and 0 <= minute < 60):
                raise ValueError
            times.append(hour * 3600 + minute * 60)
        if times[1] <= times[0]:
            raise ValueError(
                "Return departure must be later than the arrival time on the same day."
            )
        walk = payload["walk"]
        if type(walk) is not int or walk not in (5, 10, 15):
            raise ValueError
        apartments = payload.get("addresses", payload.get("apartments"))
        if not isinstance(apartments, list) or not 1 <= len(apartments) <= 3:
            raise ValueError
        normalized = []
        for i, apartment in enumerate(apartments):
            name = str(apartment["name"]).strip()
            if not name or len(name) > 200:
                raise ValueError
            rent = apartment.get("rent")
            if rent is not None and (
                type(rent) not in (int, float) or not math.isfinite(rent) or not 0 <= rent <= 100000
            ):
                raise ValueError
            normalized.append(
                {"name": name, "point": valid_point(apartment["point"]), "rent": rent, "id": i}
            )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(
            "Select a destination, 1-3 addresses, travel days, valid times "
            "and optional non-negative monthly rents."
        ) from error
    return {
        "destination": destination,
        "apartments": normalized,
        "dates": [day + timedelta(days=i) for i in weekdays],
        "arrival": times[0],
        "departure": times[1],
        "walk": walk * 60,
    }


def reverse_connections(connections: list[Connection]) -> list[Connection]:
    return [
        Connection(
            c.trip,
            c.destination,
            c.origin,
            -c.arrival,
            -c.departure,
            c.route,
            -c.sequence,
            c.can_alight,
            c.can_board,
        )
        for c in connections
    ]


def access_walks(
    stops: list[dict[str, Any]], points: list[tuple[float, float]], walk_limit: int
) -> tuple[list[dict[str, int | None]], list[dict[str, int | None]], list[list[int | None]]]:
    """Consider twelve nearby boarding stops per location, then use street durations."""
    selected: dict[str, tuple[float, float]] = {}
    for point in points:
        nearby = sorted(
            stops,
            key=lambda s: walking_seconds(point, (float(s["stop_lon"]), float(s["stop_lat"]))),
        )
        for stop in nearby[:12]:
            location = (float(stop["stop_lon"]), float(stop["stop_lat"]))
            # Geometric distance without detour allowance is a lower bound.
            if walking_seconds(point, location) / 1.3 <= walk_limit:
                selected[stop["stop_id"]] = location
    ids = list(selected)
    matrix = walking_matrix(tuple([*points, *selected.values()]))
    count = len(points)
    access, egress = [], []
    for i in range(count):
        access.append({stop: matrix[i][count + j] for j, stop in enumerate(ids)})
        egress.append({stop: matrix[count + j][i] for j, stop in enumerate(ids)})
    return access, egress, [row[:count] for row in matrix[:count]]


def select_journey(
    reachable: list[dict[str, Any]],
    access: dict[str, int | None],
    direct_walk: int | None,
    deadline: int,
    walk_limit: int,
    reverse: bool = False,
    budget: int = 5400,
) -> dict[str, Any] | None:
    candidates: list[tuple[int, list[dict[str, Any]]]] = []
    if direct_walk is not None and direct_walk <= min(walk_limit, budget):
        candidates.append(
            (
                deadline - direct_walk,
                [
                    {
                        "mode": "walk",
                        "from": "Address",
                        "to": "Destination",
                        "departure": deadline - direct_walk,
                        "arrival": deadline,
                    }
                ],
            )
        )
    for stop in reachable:
        walk = access.get(stop["stop_id"])
        if walk is None or walk > walk_limit:
            continue
        # Do not chain two walks past the per-leg limit; direct walks are above.
        if not stop["legs"] or stop["legs"][0]["mode"] != "transit":
            continue
        departure = stop["departure"] - walk
        if departure < deadline - budget:
            continue
        legs = [
            {
                "mode": "walk",
                "from": "Address",
                "to": stop["stop_name"],
                "departure": departure,
                "arrival": stop["departure"],
            },
            *[dict(leg) for leg in stop["legs"]],
        ]
        candidates.append((departure, legs))
    if not candidates:
        return None
    _, legs = max(candidates, key=lambda item: item[0])
    if reverse:
        legs = [
            dict(
                leg,
                **{
                    "from": leg["to"],
                    "to": leg["from"],
                    "departure": -leg["arrival"],
                    "arrival": -leg["departure"],
                    **(
                        {"coordinates": list(reversed(leg["coordinates"]))}
                        if "coordinates" in leg
                        else {}
                    ),
                },
            )
            for leg in reversed(legs)
        ]
    # Remove unnecessary waiting at the destination; retain actual transfer waits.
    if len(legs) > 1 and legs[-1]["mode"] == "walk":
        duration = legs[-1]["arrival"] - legs[-1]["departure"]
        legs[-1]["departure"] = legs[-2]["arrival"]
        legs[-1]["arrival"] = legs[-2]["arrival"] + duration
    if reverse and len(legs) > 1 and legs[0]["mode"] == "walk":
        duration = legs[0]["arrival"] - legs[0]["departure"]
        legs[0]["departure"] = -deadline
        legs[0]["arrival"] = -deadline + duration
    return {
        "departure": legs[0]["departure"],
        "arrival": legs[-1]["arrival"],
        "seconds": legs[-1]["arrival"] - legs[0]["departure"],
        "walking_seconds": sum(
            leg["arrival"] - leg["departure"] for leg in legs if leg["mode"] == "walk"
        ),
        "transfers": max(0, sum(leg["mode"] == "transit" for leg in legs) - 1),
        "legs": legs,
    }


def compare_day(
    stops: list[dict[str, Any]],
    morning: list[Connection],
    evening: list[Connection],
    settings: dict[str, Any],
    access: list[dict[str, int | None]],
    egress: list[dict[str, int | None]],
    direct: list[list[int | None]],
) -> list[dict[str, Any]]:
    target = settings["destination"]
    arrive, leave, walk = settings["arrival"], settings["departure"], settings["walk"]
    outbound = reachable_stops(
        stops, morning, target, arrive, 5400, walk, destination_walks=egress[0]
    )
    reversed_evening = reverse_connections(evening)
    inbound = reachable_stops(
        stops, reversed_evening, target, -leave, 5400, walk, destination_walks=access[0]
    )
    later = reachable_stops(
        stops, reversed_evening, target, -leave - 600, 5400, walk, destination_walks=access[0]
    )
    results = [
        {
            "outbound": select_journey(outbound, access[i], direct[i][0], arrive, walk),
            "return": select_journey(inbound, egress[i], direct[0][i], -leave, walk, True),
            "return_later": select_journey(
                later, egress[i], direct[0][i], -leave - 600, walk, True
            ),
        }
        for i in range(1, len(settings["apartments"]) + 1)
    ]
    for address, result in zip(settings["apartments"], results, strict=True):
        for journey in result.values():
            if not journey:
                continue
            for leg in journey["legs"]:
                if "coordinates" not in leg:
                    endpoints = {"Address": address["point"], "Destination": target}
                    if leg["from"] in endpoints and leg["to"] in endpoints:
                        leg["coordinates"] = [endpoints[leg["from"]], endpoints[leg["to"]]]
                    elif leg["from"] == "Address":
                        leg["coordinates"] = [
                            address["point"],
                            journey["legs"][1]["coordinates"][0],
                        ]
                    elif leg["to"] == "Address":
                        leg["coordinates"] = [
                            journey["legs"][-2]["coordinates"][-1],
                            address["point"],
                        ]
    return results
