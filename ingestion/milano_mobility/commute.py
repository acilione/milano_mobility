"""Arrive-by routing over scheduled connections with approximate walking links."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

WALK_METRES_PER_SECOND = 1.25
WALK_DETOUR = 1.3
TRANSFER_SECONDS = 120


def walking_seconds(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Great-circle distance with an explicit walking detour allowance."""
    lat1, lat2 = math.radians(a[1]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    metres = 12_742_000 * math.asin(min(1, math.sqrt(h)))
    return math.ceil(metres * WALK_DETOUR / WALK_METRES_PER_SECOND)


@dataclass(frozen=True)
class Connection:
    trip: str
    origin: str
    destination: str
    departure: int
    arrival: int
    route: str
    sequence: int = 0
    can_board: bool = True
    can_alight: bool = True


@dataclass(frozen=True)
class Journey:
    departure: int
    legs: tuple[dict[str, Any], ...]


def reachable_stops(
    stops: list[dict[str, Any]],
    connections: list[Connection],
    destination: tuple[float, float],
    deadline: int,
    budget: int,
    walk_limit: int,
) -> list[dict[str, Any]]:
    """Reverse connection scans, one round per boarding (at most three).

    Each round reads only the previous round's labels when changing vehicles.
    Onboard labels allow through-riding without a transfer penalty. Walking
    links are relaxed once per round so individual walks cannot chain past the
    walking limit. Times are seconds relative to the chosen local calendar day.
    """
    coordinates = {s["stop_id"]: (float(s["stop_lon"]), float(s["stop_lat"])) for s in stops}
    names = {s["stop_id"]: s["stop_name"] for s in stops}
    earliest = deadline - budget
    best: dict[str, Journey] = {}
    for stop, point in coordinates.items():
        walk = walking_seconds(point, destination)
        if walk <= min(walk_limit, budget):
            best[stop] = Journey(
                deadline - walk,
                (
                    {
                        "mode": "walk",
                        "from": names[stop],
                        "to": "Destination",
                        "departure": deadline - walk,
                        "arrival": deadline,
                    },
                ),
            )

    # Spatial buckets avoid an all-pairs distance matrix for the Milan network.
    cell = walk_limit * WALK_METRES_PER_SECOND / WALK_DETOUR / 111_000
    buckets: dict[tuple[int, int], list[str]] = {}
    for stop, (lon, lat) in coordinates.items():
        key = (math.floor(lon * math.cos(math.radians(45.46)) / cell), math.floor(lat / cell))
        buckets.setdefault(key, []).append(stop)
    neighbors: dict[str, list[tuple[str, int]]] = {}

    def nearby(stop: str) -> list[tuple[str, int]]:
        if stop not in neighbors:
            lon, lat = coordinates[stop]
            x = math.floor(lon * math.cos(math.radians(45.46)) / cell)
            y = math.floor(lat / cell)
            neighbors[stop] = []
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    for other in buckets.get((x + dx, y + dy), []):
                        seconds = walking_seconds(coordinates[other], coordinates[stop])
                        if seconds <= walk_limit:
                            neighbors[stop].append((other, seconds))
        return neighbors[stop]

    ordered = sorted(connections, key=lambda c: (c.departure, c.sequence), reverse=True)
    ready = dict(best)
    for _ in range(3):
        onboard: dict[str, Journey] = {}
        boarded: dict[str, Journey] = {}
        for c in ordered:
            if c.departure < earliest or c.arrival > deadline:
                continue
            if c.origin not in coordinates or c.destination not in coordinates:
                continue
            onward = ready.get(c.destination)
            if c.can_alight and onward is not None and c.arrival <= onward.departure:
                onboard[c.trip] = Journey(
                    c.departure,
                    (
                        {
                            "mode": "transit",
                            "route": c.route,
                            "from": names[c.origin],
                            "to": names[c.destination],
                            "departure": c.departure,
                            "arrival": c.arrival,
                        },
                        *onward.legs,
                    ),
                )
            elif c.trip in onboard:
                previous = onboard[c.trip]
                first = dict(
                    previous.legs[0], **{"from": names[c.origin], "departure": c.departure}
                )
                onboard[c.trip] = Journey(c.departure, (first, *previous.legs[1:]))
            journey = onboard.get(c.trip)
            if (
                c.can_board
                and journey
                and c.departure > boarded.get(c.origin, Journey(earliest - 1, ())).departure
            ):
                boarded[c.origin] = journey

        ready = dict(ready)
        for stop, journey in boarded.items():
            if journey.departure > best.get(stop, Journey(earliest - 1, ())).departure:
                best[stop] = journey
            for other, walk in nearby(stop):
                departure = journey.departure - walk - TRANSFER_SECONDS
                if (
                    departure >= earliest
                    and departure > ready.get(other, Journey(earliest - 1, ())).departure
                ):
                    legs = journey.legs
                    if walk:
                        legs = (
                            {
                                "mode": "walk",
                                "from": names[other],
                                "to": names[stop],
                                "departure": departure,
                                "arrival": departure + walk,
                            },
                            *legs,
                        )
                    ready[other] = Journey(departure, legs)

    return [
        dict(
            s,
            minutes=math.ceil((deadline - best[s["stop_id"]].departure) / 60),
            departure=best[s["stop_id"]].departure,
            legs=best[s["stop_id"]].legs,
        )
        for s in stops
        if s["stop_id"] in best
    ]
