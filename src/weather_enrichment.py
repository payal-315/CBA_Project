"""Enrich sensor observations with hourly, model-derived Open-Meteo temperatures."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OPENAQ_API_BASE = "https://api.openaq.org/v3"
OPEN_METEO_API_BASE = "https://historical-forecast-api.open-meteo.com/v1/forecast"
TEMPERATURE_SOURCE = (
    "Open-Meteo Historical Forecast API (model-derived; not a co-located sensor measurement)"
)

logger = logging.getLogger(__name__)


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = Request(url, headers=headers or {"Accept": "application/json"})
    try:
        with urlopen(request, timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Data provider returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach data provider: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Data provider response must be a JSON object")
    return payload


def _local_hour(value: str, timezone: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(zone)
    return parsed.strftime("%Y-%m-%dT%H:00")


def _location_details(
    location_id: int,
    api_key: str,
    cache: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    if location_id in cache:
        return cache[location_id]
    payload = _get_json(
        f"{OPENAQ_API_BASE}/locations/{location_id}",
        {"X-API-Key": api_key, "Accept": "application/json"},
    )
    results = payload.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ValueError(f"OpenAQ location {location_id} response has no location")
    location = results[0]
    coordinates = location.get("coordinates")
    if not isinstance(coordinates, dict):
        raise ValueError(f"OpenAQ location {location_id} has no coordinates")
    try:
        latitude = float(coordinates["latitude"])
        longitude = float(coordinates["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"OpenAQ location {location_id} has invalid coordinates") from exc
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError(f"OpenAQ location {location_id} coordinates are outside valid bounds")
    details = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": str(location.get("timezone") or "UTC"),
        "location_name": location.get("name"),
    }
    cache[location_id] = details
    return details


def _weather_by_local_hour(
    latitude: float,
    longitude: float,
    timezone: str,
    start_date: str,
    end_date: str,
) -> dict[str, float]:
    query = urlencode(
        {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "temperature_2m",
            "start_date": start_date,
            "end_date": end_date,
            "timezone": timezone,
        }
    )
    payload = _get_json(f"{OPEN_METEO_API_BASE}?{query}")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("Open-Meteo response did not include hourly data")
    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temperatures, list) or len(times) != len(temperatures):
        raise ValueError("Open-Meteo hourly timestamps and temperatures are inconsistent")

    values: dict[str, float] = {}
    for timestamp, temperature in zip(times, temperatures):
        if timestamp is None or temperature is None:
            continue
        values[_local_hour(str(timestamp), timezone)] = float(temperature)
    return values


def enrich_temperatures(
    records: list[dict[str, Any]],
    openaq_api_key: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Add modeled hourly temperatures while preserving existing sensor temperatures."""
    if not openaq_api_key:
        raise ValueError("Set OPENAQ_API_KEY to resolve OpenAQ sensor station locations")

    location_cache: dict[int, dict[str, Any]] = {}
    counters = {"weather_temperatures_added": 0, "weather_temperatures_unavailable": 0}
    grouped: dict[int, list[tuple[dict[str, Any], str]]] = {}

    for record in records:
        metadata = record.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("location_id") is None:
            counters["weather_temperatures_unavailable"] += 1
            continue
        if record.get("temperature") is not None:
            continue
        try:
            location_id = int(metadata["location_id"])
            location = _location_details(location_id, openaq_api_key, location_cache)
            timezone = location["timezone"]
            local_timestamp = record.get("local_timestamp")
            if not local_timestamp:
                utc_timestamp = record.get("timestamp")
                if not utc_timestamp:
                    raise ValueError("Observation has no timestamp")
                local_timestamp = datetime.fromisoformat(
                    str(utc_timestamp).replace("Z", "+00:00")
                ).astimezone(ZoneInfo(timezone)).isoformat()
            hour_key = _local_hour(str(local_timestamp), timezone)
            grouped.setdefault(location_id, []).append((record, hour_key))
        except (OSError, RuntimeError, ValueError, ZoneInfoNotFoundError):
            logger.exception(
                "Unable to add Open-Meteo temperature for location %s",
                metadata.get("location_id"),
            )
            raise

    for location_id, observations in grouped.items():
        location = location_cache[location_id]
        dates = [hour_key[:10] for _, hour_key in observations]
        try:
            temperatures = _weather_by_local_hour(
                location["latitude"],
                location["longitude"],
                location["timezone"],
                min(dates),
                max(dates),
            )
        except (OSError, RuntimeError, ValueError):
            logger.exception(
                "Unable to retrieve Open-Meteo temperatures for location %s",
                location_id,
            )
            raise
        for record, hour_key in observations:
            temperature = temperatures.get(hour_key)
            if temperature is None:
                counters["weather_temperatures_unavailable"] += 1
                continue
            record["temperature"] = temperature
            record["temperature_source"] = TEMPERATURE_SOURCE
            record["temperature_is_modelled"] = True
            metadata = record["metadata"]
            metadata.setdefault(
                "coordinates",
                {
                    "latitude": location["latitude"],
                    "longitude": location["longitude"],
                },
            )
            metadata.setdefault("timezone", location["timezone"])
            metadata.setdefault("location_name", location.get("location_name"))
            counters["weather_temperatures_added"] += 1
        time.sleep(0.1)
    return records, counters
