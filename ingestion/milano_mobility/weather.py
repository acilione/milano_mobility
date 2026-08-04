"""Daily weather enrichment ingestion for the Milan service area."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, cast

import psycopg
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from milano_mobility.config import Settings
from milano_mobility.storage import ObjectStore


class WeatherContractError(ValueError):
    """Raised when a weather provider response violates the expected contract."""


@retry(
    retry=retry_if_exception_type((requests.Timeout, requests.ConnectionError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=8),
    reraise=True,
)
def fetch_weather(
    date_from: date, date_to: date, settings: Settings
) -> tuple[dict[str, Any], list[dict[str, object]]]:
    """Fetch and normalize daily Open-Meteo observations."""
    if date_from > date_to:
        raise ValueError("date_from must not be after date_to")
    if not settings.weather_api_url:
        raise ValueError("WEATHER_API_URL must be configured before fetching weather data")
    parameters: dict[str, str | float] = {
        "latitude": settings.service_area_latitude,
        "longitude": settings.service_area_longitude,
        "start_date": date_from.isoformat(),
        "end_date": date_to.isoformat(),
        "daily": "temperature_2m_min,temperature_2m_max,precipitation_sum,weather_code",
        "timezone": settings.service_timezone,
    }
    response = requests.get(
        settings.weather_api_url,
        params=parameters,
        timeout=settings.request_timeout_seconds,
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        raise WeatherContractError("Weather response does not contain a daily object.")
    raw_fields = (
        daily.get("time"),
        daily.get("temperature_2m_min"),
        daily.get("temperature_2m_max"),
        daily.get("precipitation_sum"),
        daily.get("weather_code"),
    )
    if any(not isinstance(values, list) for values in raw_fields):
        raise WeatherContractError("Weather daily values must be arrays.")
    times, minimums, maximums, precipitation, codes = cast(
        tuple[list[Any], list[Any], list[Any], list[Any], list[Any]], raw_fields
    )
    lengths = {len(values) for values in (times, minimums, maximums, precipitation, codes)}
    if len(lengths) != 1:
        raise WeatherContractError("Weather daily arrays have inconsistent lengths.")
    rows = [
        {
            "date": date.fromisoformat(str(values[0])),
            "area_id": settings.service_area_id,
            "temperature_min_c": values[1],
            "temperature_max_c": values[2],
            "precipitation_mm": values[3],
            "weather_code": values[4],
        }
        for values in zip(
            times,
            minimums,
            maximums,
            precipitation,
            codes,
            strict=True,
        )
    ]
    return payload, rows


def backfill_weather(
    date_from: date, date_to: date, settings: Settings | None = None
) -> dict[str, object]:
    """Archive a provider response and idempotently merge normalized weather days."""
    runtime = settings or Settings()
    payload, rows = fetch_weather(date_from, date_to, runtime)
    run_id = str(uuid.uuid4())
    retrieved_at = datetime.now(timezone.utc)
    key = (
        f"weather/area_id={runtime.service_area_id}/date_from={date_from.isoformat()}/"
        f"date_to={date_to.isoformat()}/{run_id}.json"
    )
    store = ObjectStore(runtime)
    store.ensure_buckets((runtime.raw_bucket,))
    source_uri = store.put_json(runtime.raw_bucket, key, payload)
    with psycopg.connect(runtime.postgres_dsn) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT INTO staging.weather_day (
                    weather_date, area_id, temperature_min_c, temperature_max_c,
                    precipitation_mm, weather_code, source_uri, pipeline_run_id,
                    retrieved_at
                ) VALUES (
                    %(date)s, %(area_id)s, %(temperature_min_c)s,
                    %(temperature_max_c)s, %(precipitation_mm)s, %(weather_code)s,
                    %(source_uri)s, %(pipeline_run_id)s, %(retrieved_at)s
                )
                ON CONFLICT (weather_date, area_id) DO UPDATE SET
                    temperature_min_c = excluded.temperature_min_c,
                    temperature_max_c = excluded.temperature_max_c,
                    precipitation_mm = excluded.precipitation_mm,
                    weather_code = excluded.weather_code,
                    source_uri = excluded.source_uri,
                    pipeline_run_id = excluded.pipeline_run_id,
                    retrieved_at = excluded.retrieved_at
                """,
                {
                    **row,
                    "source_uri": source_uri,
                    "pipeline_run_id": run_id,
                    "retrieved_at": retrieved_at,
                },
            )
    return {
        "pipeline_run_id": run_id,
        "source_uri": source_uri,
        "row_count": len(rows),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
    }
