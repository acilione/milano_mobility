"""Bounded, cached requests to configurable address and pedestrian services."""

from __future__ import annotations

import math
import os
import time
from functools import lru_cache
from threading import Lock
from typing import Any

import requests

HEADERS = {"User-Agent": "MilanoMobility/1.0 (https://github.com/acilione/milano_mobility)"}
_GEO_LOCK = Lock()
_WALK_LOCK = Lock()
_LAST_REQUEST: dict[str, float] = {}


def _request(url: str, parameters: dict[str, str], service: str) -> Any:
    lock = _GEO_LOCK if service == "geocode" else _WALK_LOCK
    with lock:
        delay = 1.1 - (time.monotonic() - _LAST_REQUEST.get(service, 0))
        if delay > 0:
            time.sleep(delay)
        _LAST_REQUEST[service] = time.monotonic()
        response = requests.get(url, params=parameters, headers=HEADERS, timeout=20)
        response.raise_for_status()
        return response.json()


def valid_point(value: object) -> tuple[float, float]:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("Choose a location on the map or select an address result.")
    try:
        lon, lat = float(value[0]), float(value[1])
        if not (
            math.isfinite(lon) and math.isfinite(lat) and 8.3 <= lon <= 10.2 and 44.8 <= lat <= 46.2
        ):
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError("Locations must be within the Milan service area.") from error
    return lon, lat


@lru_cache(maxsize=256)
def search_places(query: str) -> list[dict[str, Any]]:
    query = query.strip()
    if not 3 <= len(query) <= 200:
        raise ValueError("Enter an address between 3 and 200 characters.")
    payload = _request(
        os.getenv("GEOCODER_URL", "https://photon.komoot.io/api/"),
        {
            "q": query,
            "limit": "12",
            "lat": "45.4642",
            "lon": "9.19",
            "bbox": "9.04,45.38,9.30,45.54",
            "lang": "en",
        },
        "geocode",
    )
    results = []
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise requests.RequestException("Invalid address search response.")
    seen: set[str] = set()
    for feature in payload["features"][:12]:
        try:
            point = valid_point(feature["geometry"]["coordinates"])
            props = feature["properties"]
            if str(props.get("city", "")).casefold() not in {"milano", "milan"}:
                continue
            street = " ".join(str(props[k]) for k in ("street", "housenumber") if props.get(k))
            name = ", ".join(
                dict.fromkeys(
                    str(v)
                    for v in (props.get("name"), street, props.get("city"), props.get("postcode"))
                    if v
                )
            )
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                results.append({"name": name, "point": point})
                if len(results) == 5:
                    break
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return results


@lru_cache(maxsize=32)
def walking_matrix(points: tuple[tuple[float, float], ...]) -> list[list[int | None]]:
    """Street walking between shortlisted locations and nearby boarding stops.

    Snap distances are included at 4.5 km/h, and snaps beyond 100 m are rejected.
    An unavailable route stays unavailable; no geometric fallback is substituted.
    """
    if not 1 <= len(points) <= 60:
        raise ValueError("Too many walking locations.")
    coordinates = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in points)
    base = os.getenv("WALK_ROUTER_URL", "https://routing.openstreetmap.de/routed-foot")
    payload = _request(
        f"{base.rstrip('/')}/table/v1/foot/{coordinates}", {"annotations": "duration"}, "walk"
    )
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        raise ValueError("Street walking routes are unavailable. Please retry.")
    for key in ("durations", "sources", "destinations"):
        if not isinstance(payload.get(key), list) or len(payload[key]) != len(points):
            raise ValueError("The walking service returned an incomplete result.")
    matrix = []
    for i, row in enumerate(payload["durations"]):
        if not isinstance(row, list) or len(row) != len(points):
            raise ValueError("The walking service returned an incomplete result.")
        converted: list[int | None] = []
        for j, value in enumerate(row):
            try:
                distances = [
                    float(payload[key][index]["distance"])
                    for key, index in (("sources", i), ("destinations", j))
                ]
                if any(not math.isfinite(d) or d < 0 for d in distances):
                    raise ValueError
                seconds = None if value is None else float(value)
                if seconds is not None and (not math.isfinite(seconds) or seconds < 0):
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                raise ValueError("The walking service returned an invalid route.") from error
            if seconds is None or max(distances) > 100:
                converted.append(None)
            else:
                converted.append(math.ceil(seconds + sum(distances) / 1.25))
        matrix.append(converted)
    return matrix
