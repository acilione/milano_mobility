from __future__ import annotations

import re
from typing import Any

import pytest

from milano_mobility import nearby
from milano_mobility.category_rules import CATEGORIES, classify_place, discovery_selectors


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"amenity": "cafe"}, "cafe"),
        ({"shop": "supermarket"}, "supermarket"),
        ({"amenity": "cinema"}, "cinema"),
        ({"shop": "books"}, "bookshop"),
        ({"amenity": "library"}, "library"),
        ({"amenity": "pharmacy"}, "pharmacy"),
        ({"amenity": "restaurant"}, "restaurant"),
        ({"leisure": "park"}, "park"),
        ({"amenity": "post_office"}, "post_office"),
        ({"amenity": "bank", "atm": "yes"}, "bank"),
        ({"amenity": "doctors"}, "healthcare"),
        ({"amenity": "clinic"}, "healthcare"),
        ({"amenity": "hospital"}, "healthcare"),
        ({"healthcare": "doctor"}, "healthcare"),
        ({"healthcare": "clinic"}, "healthcare"),
        ({"healthcare": "hospital"}, "healthcare"),
        ({"leisure": "fitness_centre"}, "gym"),
        ({"leisure": "sports_centre", "sport": "fitness"}, "gym"),
        ({"leisure": "sports_centre", "sport": "swimming; fitness"}, "gym"),
        ({"leisure": "fitness_centre", "swimming_pool": "yes"}, "gym"),
    ],
)
def test_explicit_service_evidence(tags: dict[str, str], expected: str) -> None:
    result = classify_place(tags)
    assert list(result) == [expected]
    assert result[expected]
    assert all(tags[key].strip().lower() == value for key, value in result[expected].items())


@pytest.mark.parametrize(
    "tags",
    [
        {"shop": "coffee", "name": "Cafe Milano"},
        {"shop": "bakery"},
        {"amenity": "bar"},
        {"shop": "convenience", "name": "Supermarket"},
        {"shop": "greengrocer"},
        {"amenity": "theatre", "name": "Cinema"},
        {"amenity": "arts_centre"},
        {"shop": "stationery", "books": "yes"},
        {"amenity": "public_bookcase"},
        {"office": "archive", "name": "Library"},
        {"shop": "chemist", "name": "Pharmacy"},
        {"shop": "herbalist"},
        {"amenity": "fast_food", "name": "Restaurant"},
        {"leisure": "garden", "access": "yes"},
        {"leisure": "playground"},
        {"landuse": "grass", "name": "Park"},
        {"amenity": "post_box"},
        {"amenity": "parcel_locker"},
        {"amenity": "atm", "brand": "Bank"},
        {"office": "financial"},
        {"building": "hospital"},
        {"healthcare": "yes"},
        {"leisure": "sports_centre"},
        {"leisure": "sports_centre", "sport": "swimming"},
        {"leisure": "fitness_centre", "sport": "tennis"},
        {"leisure": "fitness_station", "sport": "fitness"},
        {"leisure": "swimming_pool", "sport": "fitness"},
        {"amenity": "cafe;restaurant"},
        {"shop": "yes", "brand": "Supermarket"},
        {"name": "Café Library Restaurant Gym"},
        {"amenity": ["cafe"]},
        None,
    ],
)
def test_related_types_and_names_are_not_category_evidence(tags: Any) -> None:
    assert classify_place(tags) == {}


@pytest.mark.parametrize("category", list(CATEGORIES))
def test_every_category_excludes_inactive_and_private_records(category: str) -> None:
    tags = {key: values[0] for key, values in CATEGORIES[category]["matches"][0].items()}
    primary_key = next(iter(tags))
    for marker in (
        {"access": "private"},
        {"access": "no"},
        {"disused": "yes"},
        {"closed": "yes"},
        {"construction": "yes"},
        {f"abandoned:{primary_key}": tags[primary_key]},
        {f"disused:{primary_key}": tags[primary_key]},
    ):
        assert classify_place(dict(tags, **marker)) == {}
    assert category in classify_place(dict(tags, access="customers", fee="yes"))


def test_independent_services_and_lifecycle_conflicts() -> None:
    # Two explicitly mapped services can coexist; one service is not inferred from the other.
    assert set(classify_place({"amenity": "cafe", "shop": "books"})) == {"cafe", "bookshop"}
    assert classify_place({"amenity": "cafe", "disused:shop": "books"}) == {
        "cafe": {"amenity": "cafe"}
    }
    assert (
        classify_place({"amenity": "clinic", "healthcare": "clinic", "disused:amenity": "clinic"})
        == {}
    )


def test_discovery_requires_all_tags_and_supports_shared_definitions() -> None:
    selectors = discovery_selectors((9.2, 45.46), 1000)
    sports = [s for s in selectors if "sports_centre" in s]
    assert len(sports) == 1 and '["sport"~' in sports[0]
    assert not any(value in "".join(selectors) for value in ("convenience", "garden", "atm"))
    for rule in CATEGORIES.values():
        assert rule["description"] and rule["label"]
        for match in rule["matches"]:
            assert all(
                re.fullmatch(r"[a-z_]+", value) for values in match.values() for value in values
            )


def test_discovery_keeps_category_evidence_and_access_for_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nearby._places.cache_clear()

    class Response:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict[str, Any]:
            return {
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "lat": 45.46,
                        "lon": 9.2,
                        "tags": {"amenity": "clinic", "access": "customers", "fee": "yes"},
                    },
                    {
                        "type": "node",
                        "id": 2,
                        "lat": 45.46,
                        "lon": 9.2,
                        "tags": {"shop": "convenience", "name": "Supermarket"},
                    },
                ]
            }

    monkeypatch.setattr(nearby.requests, "post", lambda *args, **kwargs: Response())
    try:
        places = nearby._places((9.2, 45.46), 1000, "today")["places"]
        assert len(places) == 1
        assert places[0]["name"] == "Unnamed mapped place"
        assert places[0]["classification"] == {"healthcare": {"amenity": "clinic"}}
        assert places[0]["source_tags"]["access"] == "customers"
        assert places[0]["source_tags"]["fee"] == "yes"
    finally:
        nearby._places.cache_clear()
