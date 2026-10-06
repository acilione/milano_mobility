"""Pedestrian route geometry and bounded journey map assembly."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import requests

from milano_mobility.commute import walking_seconds
from milano_mobility.places import _request, valid_point


@lru_cache(maxsize=256)
def walking_route(origin: tuple[float, float], destination: tuple[float, float]) -> dict[str, Any]:
    base = os.getenv("WALK_ROUTER_URL", "https://routing.openstreetmap.de/routed-foot")
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in (origin, destination))
    payload = _request(
        f"{base.rstrip('/')}/route/v1/foot/{coords}",
        {
            "overview": "full",
            "geometries": "geojson",
            "steps": "true",
            "alternatives": "false",
        },
        "walk",
    )
    try:
        if payload["code"] != "Ok":
            raise ValueError
        route = payload["routes"][0]
        snaps = [float(p["distance"]) for p in payload["waypoints"]]
        if len(snaps) != 2 or any(not math.isfinite(s) or s < 0 or s > 100 for s in snaps):
            raise ValueError
        geometry = [valid_point(p) for p in route["geometry"]["coordinates"]]
        if not 2 <= len(geometry) <= 20000:
            raise ValueError
        duration, distance = float(route["duration"]), float(route["distance"])
        if any(not math.isfinite(v) or v < 0 for v in (duration, distance)):
            raise ValueError
        steps = []
        for leg in route["legs"]:
            for step in leg["steps"]:
                maneuver = step["maneuver"]
                action = str(maneuver.get("type", "continue")).replace("_", " ")
                modifier = str(maneuver.get("modifier", ""))
                name = str(step.get("name") or step.get("ref") or "")
                instruction = " ".join(
                    v for v in (action.capitalize(), modifier, f"— {name}" if name else "") if v
                )
                steps.append({"instruction": instruction, "metres": round(float(step["distance"]))})
        return {
            "coordinates": geometry,
            "seconds": math.ceil(duration + sum(snaps) / 1.25),
            "metres": math.ceil(distance + sum(snaps)),
            "steps": steps,
            "access": [[origin, geometry[0]], [geometry[-1], destination]],
        }
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise requests.RequestException(
            "A pedestrian route could not be verified for these locations."
        ) from error


def clip_shape(
    shape: list[tuple[float, float]], stops: list[tuple[float, float]]
) -> list[tuple[float, float]] | None:
    """Match stops in order to a directed shape, including repeated coordinates on loops."""
    if len(shape) < 2:
        return None
    position = 0
    matched = []
    for stop in stops:
        position = min(range(position, len(shape)), key=lambda i: walking_seconds(stop, shape[i]))
        if walking_seconds(stop, shape[position]) > 600:
            return None
        matched.append(position)
    if matched[-1] <= matched[0]:
        return None
    return shape[matched[0] : matched[-1] + 1]


def journey_map(
    payload: Any, shape_loader: Callable[[str], list[tuple[float, float]]]
) -> dict[str, Any]:
    try:
        legs = payload["legs"]
        if not isinstance(legs, list) or not 1 <= len(legs) <= 12:
            raise ValueError
        normalized = []
        for leg in legs:
            if leg["mode"] not in {"walk", "transit"} or not 2 <= len(leg["coordinates"]) <= 200:
                raise ValueError
            points = [valid_point(p) for p in leg["coordinates"]]
            trip = str(leg.get("trip_id", ""))
            if len(trip) > 200:
                raise ValueError
            normalized.append((leg, points, trip))
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Select a calculated journey to display its route.") from error
    features, notes = [], []
    for i, (leg, points, trip) in enumerate(normalized):
        kind = "scheduled_stops"
        if leg["mode"] == "walk":
            try:
                path = walking_route(points[0], points[-1])
                points = path["coordinates"]
                kind = "street_walk"
                for access in path["access"]:
                    features.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "kind": "access",
                                "label": "Approximate access",
                                "leg": i,
                            },
                            "geometry": {"type": "LineString", "coordinates": access},
                        }
                    )
            except requests.RequestException:
                kind = "unverified_walk"
                notes.append(
                    "Some walking paths are unavailable; dashed links show endpoints only."
                )
        else:
            shape = shape_loader(trip) if trip else []
            clipped = clip_shape(shape, points)
            if clipped:
                points, kind = clipped, "official_transit"
            else:
                notes.append(
                    "Some transit paths connect scheduled stops; "
                    "exact track geometry is unavailable."
                )
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": kind, "label": str(leg.get("route", "Walk")), "leg": i},
                "geometry": {"type": "LineString", "coordinates": points},
            }
        )
    return {"type": "FeatureCollection", "features": features, "notes": list(dict.fromkeys(notes))}
