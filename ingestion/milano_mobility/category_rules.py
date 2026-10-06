"""Evidence-based place classification shared with the comparison interface.

Definitions describe services, not names, brands, building shapes or broad parent types.
Multiple categories require independent matching evidence. Unsupported or conflicting
values are excluded rather than assigned to the nearest available category.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATALOG = json.loads(Path(__file__).with_name("category_rules.json").read_text(encoding="utf-8"))
CATEGORIES: dict[str, dict[str, Any]] = {c["id"]: c for c in CATALOG["categories"]}
LIFECYCLE = ("disused", "abandoned", "demolished", "removed", "razed", "construction", "proposed")
TYPE_KEYS = ("amenity", "shop", "leisure", "healthcare")
EVIDENCE_KEYS = (*TYPE_KEYS, "sport", "access", "membership", "fee")


def _values(key: str, value: str) -> set[str]:
    # sport supports multiple activities; ambiguous primary facility types do not.
    return {part.strip() for part in value.split(";")} if key == "sport" else {value}


def _active(tags: dict[str, str], key: str) -> bool:
    return not any(tags.get(f"{prefix}:{key}") in {tags.get(key), "yes"} for prefix in LIFECYCLE)


def classify_place(raw_tags: Any) -> dict[str, dict[str, str]]:
    """Return each supported category with the tags that justify its inclusion."""
    if not isinstance(raw_tags, dict):
        return {}
    tags = {k: v.strip().lower() for k, v in raw_tags.items() if isinstance(v, str)}
    if (
        tags.get("access") in {"private", "no"}
        or any(tags.get(key) == "yes" for key in (*LIFECYCLE, "closed"))
        or any(not _active(tags, key) for key in TYPE_KEYS if key in tags)
    ):
        return {}
    result = {}
    for category, rule in CATEGORIES.items():
        if any(
            key in tags and not (_values(key, tags[key]) & set(allowed))
            for key, allowed in rule.get("optional_constraints", {}).items()
        ):
            continue
        for match in rule["matches"]:
            if all(
                key in tags and _active(tags, key) and _values(key, tags[key]) & set(allowed)
                for key, allowed in match.items()
            ):
                result[category] = {key: tags[key] for key in match}
                result[category].update(
                    {key: tags[key] for key in rule.get("optional_constraints", {}) if key in tags}
                )
                break
    return result


def discovery_selectors(point: tuple[float, float], radius: int) -> list[str]:
    """Generate candidate queries from the same positive rules as classification."""
    lon, lat = point
    selectors = set()
    for rule in CATEGORIES.values():
        for match in rule["matches"]:
            filters = []
            for key, values in match.items():
                pattern = f"({'|'.join(values)})"
                pattern = (
                    f"(^|;)[[:space:]]*{pattern}[[:space:]]*(;|$)"
                    if key == "sport"
                    else f"^{pattern}$"
                )
                filters.append(f'["{key}"~"{pattern}"]')
            selectors.add(f"nwr(around:{radius},{lat:.6f},{lon:.6f}){''.join(filters)};")
    return sorted(selectors)
