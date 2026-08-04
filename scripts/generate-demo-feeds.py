"""Generate deterministic, visually rich GTFS demo snapshots."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FIELDNAMES = {
    "agency.txt": [
        "agency_id",
        "agency_name",
        "agency_url",
        "agency_timezone",
        "agency_lang",
    ],
    "stops.txt": [
        "stop_id",
        "stop_code",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "zone_id",
        "location_type",
        "parent_station",
    ],
    "routes.txt": [
        "route_id",
        "agency_id",
        "route_short_name",
        "route_long_name",
        "route_desc",
        "route_type",
        "route_color",
        "route_text_color",
    ],
    "trips.txt": [
        "route_id",
        "service_id",
        "trip_id",
        "trip_headsign",
        "direction_id",
        "block_id",
        "shape_id",
    ],
    "stop_times.txt": [
        "trip_id",
        "arrival_time",
        "departure_time",
        "stop_id",
        "stop_sequence",
        "stop_headsign",
        "pickup_type",
        "drop_off_type",
    ],
    "calendar.txt": [
        "service_id",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
        "start_date",
        "end_date",
    ],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
}

AGENCY = [
    {
        "agency_id": "ATM",
        "agency_name": "Azienda Trasporti Milanesi",
        "agency_url": "https://www.atm.it",
        "agency_timezone": "Europe/Rome",
        "agency_lang": "en",
    }
]
CALENDAR = [
    {
        "service_id": "DAILY",
        "monday": 1,
        "tuesday": 1,
        "wednesday": 1,
        "thursday": 1,
        "friday": 1,
        "saturday": 1,
        "sunday": 1,
        "start_date": "20260728",
        "end_date": "20260831",
    }
]
CALENDAR_DATES = [{"service_id": "DAILY", "date": "20260815", "exception_type": 2}]

BASE_STOPS = [
    ("S1", "DUOMO", "Duomo", "45.464170", "9.189990"),
    ("S2", "CENTRALE", "Centrale FS", "45.485890", "9.204440"),
    ("S3", "CADORNA", "Cadorna FN", "45.468600", "9.176590"),
    ("S4", "PORTA_VENEZIA", "Porta Venezia", "45.474650", "9.205290"),
    ("S5", "GARIBALDI", "Garibaldi FS", "45.484190", "9.187790"),
    ("S6", "LORETO", "Loreto", "45.484720", "9.215180"),
    ("S7", "SANT_AMBROGIO", "Sant'Ambrogio", "45.462340", "9.175830"),
    ("S8", "PORTA_ROMANA", "Porta Romana", "45.451820", "9.202280"),
    ("S9", "PORTA_GENOVA", "Porta Genova FS", "45.452420", "9.169080"),
    ("S10", "SAN_SIRO", "San Siro Stadio", "45.478130", "9.123970"),
    ("S11", "LINATE", "Linate Aeroporto", "45.449720", "9.278310"),
    ("S12", "BICOCCA", "Bicocca", "45.513520", "9.205160"),
    ("S13", "LOTTO", "Lotto", "45.479000", "9.143200"),
    ("S14", "MISSORI", "Missori", "45.460330", "9.188160"),
    ("S15", "ISOLA", "Isola", "45.488720", "9.190690"),
]

BASE_ROUTES = [
    (
        "R1",
        "M1",
        "Sesto 1 Maggio FS - Rho Fieramilano/Bisceglie",
        "Red metro line",
        1,
        "E51B23",
        "FFFFFF",
    ),
    (
        "R2",
        "M2",
        "Assago Milanofiori Forum - Cologno Nord/Gessate",
        "Green metro line",
        1,
        "009D58",
        "FFFFFF",
    ),
    ("R3", "M3", "Comasina - San Donato", "Yellow metro line", 1, "FFD500", "111111"),
    ("R4", "M4", "San Cristoforo - Linate Aeroporto", "Blue metro line", 1, "0072CE", "FFFFFF"),
    ("R5", "M5", "San Siro Stadio - Bignami", "Lilac metro line", 1, "8A2BE2", "FFFFFF"),
    ("R6", "1", "Greco - Roserio", "Historic tram line", 0, "F28C28", "111111"),
    ("R7", "90", "External circular line", "Night and orbital trolleybus", 3, "5B9BD5", "FFFFFF"),
]


@dataclass(frozen=True)
class TripPattern:
    route_id: str
    trip_id: str
    headsign: str
    direction_id: int
    departure: str
    stops: tuple[str, ...]


def _seconds(value: str) -> int:
    hours, minutes, seconds = (int(part) for part in value.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _time(value: int) -> str:
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _patterns(version: int) -> list[TripPattern]:
    paths: dict[str, tuple[str, ...]] = {
        "R1": ("S10", "S13", "S3", "S1", "S4", "S6"),
        "R2": ("S9" if version == 1 else "S16", "S7", "S3", "S5", "S2", "S6"),
        "R3": ("S8", "S14", "S1", "S2", "S12"),
        "R4": ("S11", "S4", "S1", "S7"),
        "R5": ("S10", "S13", "S5", "S15", "S12"),
        "R6": ("S3", "S1", "S14", "S8"),
        "R7": ("S10", "S13", "S5", "S2", "S6", "S12"),
    }
    definitions = {
        "R1": (
            ("05:35:00", "16:15:00", "20:40:00", "24:10:00"),
            ("Sesto 1 Maggio FS", "Rho Fieramilano"),
        ),
        "R2": (("06:10:00", "09:20:00", "17:30:00", "22:45:00"), ("Cologno Nord", "Assago Forum")),
        "R3": (("05:50:00", "08:40:00", "18:05:00", "23:30:00"), ("San Donato", "Comasina")),
        "R4": (
            ("06:25:00", "10:15:00", "17:50:00", "21:40:00"),
            ("Linate Aeroporto", "San Cristoforo"),
        ),
        "R5": (("05:45:00", "07:55:00", "16:45:00", "20:30:00"), ("Bignami", "San Siro Stadio")),
        "R6": (("06:35:00", "11:00:00", "18:20:00", "23:50:00"), ("Greco", "Roserio")),
        "R7": (("05:20:00", "07:20:00", "15:40:00", "24:35:00"), ("Lodi M3", "Circular service")),
    }
    patterns: list[TripPattern] = []
    for route_id, path in paths.items():
        departures, termini = definitions[route_id]
        for index, departure in enumerate(departures):
            reverse = index % 2 == 1
            patterns.append(
                TripPattern(
                    route_id=route_id,
                    trip_id=f"{route_id}_T{index + 1}",
                    headsign=termini[1 if reverse else 0],
                    direction_id=1 if reverse else 0,
                    departure=departure,
                    stops=tuple(reversed(path)) if reverse else path,
                )
            )
    if version == 2:
        extra_path = ("S12", "S5", "S1", "S8")
        for index, departure in enumerate(("07:05:00", "12:10:00", "18:35:00", "23:15:00")):
            reverse = index % 2 == 1
            patterns.append(
                TripPattern(
                    route_id="R8",
                    trip_id=f"R8_T{index + 1}",
                    headsign="Rozzano" if reverse else "Duomo",
                    direction_id=1 if reverse else 0,
                    departure=departure,
                    stops=tuple(reversed(extra_path)) if reverse else extra_path,
                )
            )
    return patterns


def _stops(version: int) -> list[dict[str, str | int]]:
    values = BASE_STOPS
    if version == 2:
        values = [stop for stop in BASE_STOPS if stop[0] != "S9"]
        values = [
            (
                stop_id,
                code,
                "Milano Centrale FS" if stop_id == "S2" else name,
                "45.485950" if stop_id == "S2" else latitude,
                "9.204500" if stop_id == "S2" else longitude,
            )
            for stop_id, code, name, latitude, longitude in values
        ]
        values.append(("S16", "DARSENA", "Darsena", "45.452180", "9.177120"))
    return [
        {
            "stop_id": stop_id,
            "stop_code": code,
            "stop_name": name,
            "stop_lat": latitude,
            "stop_lon": longitude,
            "zone_id": "Mi1",
            "location_type": 0,
            "parent_station": "",
        }
        for stop_id, code, name, latitude, longitude in values
    ]


def _routes(version: int) -> list[dict[str, str | int]]:
    routes = list(BASE_ROUTES)
    if version == 2:
        routes.append(
            ("R8", "15", "Duomo - Rozzano", "Southern tram corridor", 0, "B56A3D", "FFFFFF")
        )
    return [
        {
            "route_id": route_id,
            "agency_id": "ATM",
            "route_short_name": short_name,
            "route_long_name": long_name,
            "route_desc": description,
            "route_type": route_type,
            "route_color": color,
            "route_text_color": text_color,
        }
        for route_id, short_name, long_name, description, route_type, color, text_color in routes
    ]


def _trips(patterns: list[TripPattern]) -> list[dict[str, str | int]]:
    return [
        {
            "route_id": pattern.route_id,
            "service_id": "DAILY",
            "trip_id": pattern.trip_id,
            "trip_headsign": pattern.headsign,
            "direction_id": pattern.direction_id,
            "block_id": f"B{index:02d}",
            "shape_id": "",
        }
        for index, pattern in enumerate(patterns, start=1)
    ]


def _stop_times(patterns: list[TripPattern]) -> list[dict[str, str | int]]:
    rows: list[dict[str, str | int]] = []
    for pattern in patterns:
        start = _seconds(pattern.departure)
        for sequence, stop_id in enumerate(pattern.stops, start=1):
            arrival = start + (sequence - 1) * 420
            rows.append(
                {
                    "trip_id": pattern.trip_id,
                    "arrival_time": _time(arrival),
                    "departure_time": _time(arrival + 30),
                    "stop_id": stop_id,
                    "stop_sequence": sequence,
                    "stop_headsign": "",
                    "pickup_type": 0,
                    "drop_off_type": 0,
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDNAMES[path.name], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _write_zip(source: Path, target: Path) -> None:
    with ZipFile(target, "w") as archive:
        for name in FIELDNAMES:
            info = ZipInfo(name, date_time=(2026, 7, 28, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (source / name).read_bytes())


def generate(version: int) -> None:
    source = FIXTURES / f"v{version}"
    source.mkdir(parents=True, exist_ok=True)
    patterns = _patterns(version)
    tables: dict[str, list[dict[str, object]]] = {
        "agency.txt": AGENCY,
        "stops.txt": _stops(version),
        "routes.txt": _routes(version),
        "trips.txt": _trips(patterns),
        "stop_times.txt": _stop_times(patterns),
        "calendar.txt": CALENDAR,
        "calendar_dates.txt": CALENDAR_DATES,
    }
    for name, rows in tables.items():
        _write_csv(source / name, rows)
    _write_zip(source, FIXTURES / f"gtfs_v{version}.zip")
    print(
        f"v{version}: {len(tables['routes.txt'])} routes, "
        f"{len(tables['stops.txt'])} stops, {len(tables['trips.txt'])} trips, "
        f"{len(tables['stop_times.txt'])} stop times"
    )


if __name__ == "__main__":
    generate(1)
    generate(2)
