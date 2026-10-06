"""Bounded OpenStreetMap place discovery and pedestrian access comparison."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from functools import lru_cache
from threading import Lock
from typing import Any

import requests

from milano_mobility.category_rules import (
    CATALOG,
    CATEGORIES,
    EVIDENCE_KEYS,
    classify_place,
    discovery_selectors,
)
from milano_mobility.commute import walking_seconds
from milano_mobility.places import HEADERS, _request, valid_point

_POI_LOCK = Lock()
CANDIDATE_LIMIT = 20


@lru_cache(maxsize=64)
def _places(point: tuple[float, float], radius: int, day: str) -> dict[str, Any]:
    selectors = discovery_selectors(point, radius)
    query = "[out:json][timeout:25][maxsize:16777216];(" + "".join(selectors) + ");out center tags;"
    with _POI_LOCK:
        response = requests.post(
            os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
            data={"data": query},
            headers=HEADERS,
            timeout=40,
        )
        response.raise_for_status()
        payload = response.json()
    if (
        not isinstance(payload, dict)
        or payload.get("remark")
        or not isinstance(payload.get("elements"), list)
    ):
        raise requests.RequestException("Nearby place data is incomplete. Please retry.")
    if len(payload["elements"]) > 5000:
        raise ValueError("Too many nearby places. Choose a smaller search radius.")
    places = []
    seen = set()
    for element in payload["elements"]:
        try:
            tags = element["tags"]
            classification = classify_place(tags)
            if not classification:
                continue
            center = element.get("center", element)
            position = valid_point([center["lon"], center["lat"]])
            # Polygon centres may lie beyond the requested radius.
            if walking_seconds(point, position) * 1.25 / 1.3 > radius:
                continue
            identifier = f"{element['type']}/{int(element['id'])}"
            if element["type"] not in {"node", "way", "relation"} or identifier in seen:
                continue
            seen.add(identifier)
            categories = list(classification)
            places.append(
                {
                    "id": identifier,
                    "name": str(tags.get("name") or tags.get("brand") or "Unnamed mapped place"),
                    "point": position,
                    "categories": categories,
                    "classification": classification,
                    "source_tags": {
                        key: tags[key] for key in EVIDENCE_KEYS if isinstance(tags.get(key), str)
                    },
                    "address": " ".join(
                        str(tags[k]) for k in ("addr:street", "addr:housenumber") if tags.get(k)
                    ),
                    "opening_hours": str(tags.get("opening_hours", "")),
                    "osm_url": f"https://www.openstreetmap.org/{identifier}",
                }
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return {
        "places": places,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "osm_timestamp": payload.get("osm3s", {}).get("timestamp_osm_base"),
    }


@lru_cache(maxsize=128)
def walking_times(
    origin: tuple[float, float], destinations: tuple[tuple[float, float], ...]
) -> list[int | None]:
    """One source per table; missing routes stay unknown, never straight-line estimates."""
    if not destinations or len(destinations) > 49:
        raise ValueError("Choose between 1 and 49 destinations.")
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in (origin, *destinations))
    base = os.getenv("WALK_ROUTER_URL", "https://routing.openstreetmap.de/routed-foot")
    payload = _request(
        f"{base.rstrip('/')}/table/v1/foot/{coords}",
        {
            "sources": "0",
            "destinations": ";".join(str(i) for i in range(1, len(destinations) + 1)),
            "annotations": "duration",
        },
        "walk",
    )
    try:
        if (
            payload["code"] != "Ok"
            or len(payload["durations"]) != 1
            or len(payload["durations"][0]) != len(destinations)
        ):
            raise ValueError
        start = float(payload["sources"][0]["distance"])
        result: list[int | None] = []
        for i, value in enumerate(payload["durations"][0]):
            end = float(payload["destinations"][i]["distance"])
            if not all(math.isfinite(v) and v >= 0 for v in (start, end)):
                raise ValueError
            if value is None or max(start, end) > 100:
                result.append(None)
            else:
                seconds = float(value)
                if not math.isfinite(seconds) or seconds < 0:
                    raise ValueError
                result.append(math.ceil(seconds + (start + end) / 1.25))
        return result
    except (KeyError, TypeError, ValueError, IndexError) as error:
        raise requests.RequestException("Walking times are unavailable. Please retry.") from error


def compare_nearby(payload: Any) -> dict[str, Any]:
    try:
        addresses = payload["addresses"]
        if not isinstance(addresses, list) or not 1 <= len(addresses) <= 3:
            raise ValueError
        normalized = []
        for address in addresses:
            name = address["name"]
            if not isinstance(name, str) or not 1 <= len(name) <= 200:
                raise ValueError
            normalized.append({"name": name, "point": valid_point(address["point"])})
        categories = list(dict.fromkeys(payload["categories"]))
        if not categories or any(c not in CATEGORIES for c in categories):
            raise ValueError
        radius, minutes = payload["radius"], payload["minutes"]
        if (
            type(radius) is not int
            or radius not in (500, 1000, 1500)
            or type(minutes) is not int
            or minutes not in (5, 10, 15, 20)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "Select 1-3 addresses, categories, a search radius and a walking limit."
        ) from error
    output = []
    for address in normalized:
        try:
            source = _places(
                address["point"], radius, datetime.now(timezone.utc).date().isoformat()
            )
            selected: dict[str, dict[str, Any]] = {}
            groups: dict[str, dict[str, Any]] = {}
            for category in categories:
                candidates = sorted(
                    (p for p in source["places"] if category in p["categories"]),
                    key=lambda p: walking_seconds(address["point"], p["point"]),
                )
                shortlist = candidates[:CANDIDATE_LIMIT]
                groups[category] = {
                    "mapped_count": len(candidates),
                    "sampled": len(candidates) > CANDIDATE_LIMIT,
                    "ids": [p["id"] for p in shortlist],
                }
                selected.update((p["id"], dict(p)) for p in shortlist)
            places = list(selected.values())
            for start in range(0, len(places), 49):
                batch = places[start : start + 49]
                times = walking_times(address["point"], tuple(p["point"] for p in batch))
                for place, seconds in zip(batch, times, strict=True):
                    place["seconds"] = seconds
            for group in groups.values():
                candidates = [selected[key] for key in group.pop("ids")]
                reachable = sorted(
                    (
                        p
                        for p in candidates
                        if p["seconds"] is not None and p["seconds"] <= minutes * 60
                    ),
                    key=lambda p: p["seconds"],
                )
                group.update(
                    places=reachable,
                    reachable_count=len(reachable),
                    checked_count=len(candidates),
                    unknown_count=sum(p["seconds"] is None for p in candidates),
                    nearest_seconds=min(
                        (p["seconds"] for p in candidates if p["seconds"] is not None), default=None
                    ),
                )
            output.append(
                dict(
                    address,
                    status="ready",
                    categories=groups,
                    retrieved_at=source["retrieved_at"],
                    osm_timestamp=source["osm_timestamp"],
                )
            )
        except requests.RequestException:
            output.append(
                dict(
                    address,
                    status="unavailable",
                    reason="Place data or walking routes are unavailable. Retry this comparison.",
                )
            )
    return {
        "addresses": output,
        "categories": [
            {"id": c, "label": CATEGORIES[c]["label"], "description": CATEGORIES[c]["description"]}
            for c in categories
        ],
        "classification_version": CATALOG["version"],
        "radius": radius,
        "minutes": minutes,
        "candidate_limit": CANDIDATE_LIMIT,
    }
