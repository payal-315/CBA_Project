"""Download OpenAQ v3 hourly measurements to an immutable local JSON snapshot."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

API_BASE = "https://api.openaq.org/v3"
DEFAULT_BENGALURU_COORDINATES = (12.9716, 77.5946)


def _validate_datetime(value: str, option: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{option} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{option} must include a timezone (for example, Z)")
    return value


def download_hourly(
    sensor_id: int,
    datetime_from: str,
    datetime_to: str,
    api_key: str,
    limit: int = 1000,
) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Set OPENAQ_API_KEY before downloading from OpenAQ")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    results: list[dict[str, Any]] = []
    page = 1
    found: int | None = None
    while found is None or len(results) < found:
        query = urlencode(
            {
                "datetime_from": datetime_from,
                "datetime_to": datetime_to,
                "limit": limit,
                "page": page,
            }
        )
        request = Request(
            f"{API_BASE}/sensors/{sensor_id}/hours?{query}",
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAQ returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach OpenAQ: {exc.reason}") from exc
        page_results = payload.get("results")
        if not isinstance(page_results, list):
            raise ValueError("OpenAQ response did not include a results array")
        results.extend(page_results)
        found_value = (payload.get("meta") or {}).get("found")
        if found_value is not None:
            found = int(found_value)
        if not page_results:
            break
        if found is not None and len(results) >= found:
            break
        if found is None and len(page_results) < limit:
            break
        page += 1
        time.sleep(0.15)

    return {
        "source": "OpenAQ v3",
        "sensor_ids": [sensor_id],
        "datetime_from": datetime_from,
        "datetime_to": datetime_to,
        "results": results,
    }


def discover_pm25_sensors(
    latitude: float,
    longitude: float,
    radius: int,
    api_key: str,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Find PM2.5 sensors at OpenAQ locations within the requested radius."""
    if not api_key:
        raise ValueError("Set OPENAQ_API_KEY before downloading from OpenAQ")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("coordinates must be valid latitude/longitude values")
    if not 1 <= radius <= 25_000:
        raise ValueError("radius must be between 1 and 25000 meters")
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")

    sensors: dict[int, dict[str, Any]] = {}
    page = 1
    found: int | None = None
    while found is None or (page - 1) * limit < found:
        query = urlencode(
            {
                "coordinates": f"{latitude},{longitude}",
                "radius": radius,
                "parameters_id": 2,
                "limit": limit,
                "page": page,
            }
        )
        request = Request(
            f"{API_BASE}/locations?{query}",
            headers={"X-API-Key": api_key, "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAQ returned HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Could not reach OpenAQ: {exc.reason}") from exc

        locations = payload.get("results")
        if not isinstance(locations, list):
            raise ValueError("OpenAQ locations response did not include a results array")
        for location in locations:
            if not isinstance(location, dict):
                continue
            for sensor in location.get("sensors") or []:
                if not isinstance(sensor, dict) or sensor.get("id") is None:
                    continue
                parameter = sensor.get("parameter") or {}
                parameter_id = parameter.get("id")
                parameter_name = str(parameter.get("name") or "").lower().replace(".", "")
                if parameter_id != 2 and parameter_name not in {"pm25", "pm2_5"}:
                    continue
                sensor_id = int(sensor["id"])
                sensors[sensor_id] = {
                    "id": sensor_id,
                    "location_id": location.get("id"),
                    "location_name": location.get("name"),
                    "coordinates": location.get("coordinates"),
                    "parameter": parameter,
                }

        found_value = (payload.get("meta") or {}).get("found")
        if found_value is not None:
            found = int(found_value)
        if not locations or (found is not None and page * limit >= found):
            break
        if found is None and len(locations) < limit:
            break
        page += 1
        time.sleep(0.15)
    return list(sensors.values())


def download_sensors_hourly(
    sensors: list[dict[str, Any]],
    datetime_from: str,
    datetime_to: str,
    api_key: str,
    limit: int = 1000,
) -> dict[str, Any]:
    """Download hourly pages for each discovered sensor into one snapshot."""
    if not sensors:
        raise ValueError("No matching PM2.5 sensors were found for the selected area")
    results: list[dict[str, Any]] = []
    for sensor in sensors:
        batch = download_hourly(
            int(sensor["id"]), datetime_from, datetime_to, api_key, limit=limit
        )
        for measurement in batch["results"]:
            measurement.setdefault("sensorsId", int(sensor["id"]))
            measurement.setdefault("locationsId", sensor.get("location_id"))
            measurement.setdefault("location", sensor.get("location_name"))
            measurement.setdefault("coordinates", sensor.get("coordinates"))
        results.extend(batch["results"])
        time.sleep(0.15)
    return {
        "source": "OpenAQ v3",
        "sensor_ids": [int(sensor["id"]) for sensor in sensors],
        "datetime_from": datetime_from,
        "datetime_to": datetime_to,
        "results": results,
    }


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sensor-id", type=int, help="Download one known OpenAQ sensor")
    parser.add_argument(
        "--coordinates",
        nargs=2,
        type=float,
        metavar=("LATITUDE", "LONGITUDE"),
        help="Discover nearby PM2.5 sensors; defaults to the project sample's Bengaluru area",
    )
    parser.add_argument("--radius", type=int, default=25_000, help="Discovery radius in meters (max 25000)")
    parser.add_argument("--from", dest="datetime_from", required=True)
    parser.add_argument("--to", dest="datetime_to", required=True)
    parser.add_argument("--output", type=Path, default=Path("data/raw/json/openaq_hourly.json"))
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if args.sensor_id is not None and args.coordinates is not None:
        parser.error("Use either --sensor-id or --coordinates, not both")
    args.datetime_from = _validate_datetime(args.datetime_from, "--from")
    args.datetime_to = _validate_datetime(args.datetime_to, "--to")
    start = datetime.fromisoformat(args.datetime_from.replace("Z", "+00:00"))
    end = datetime.fromisoformat(args.datetime_to.replace("Z", "+00:00"))
    if start >= end:
        parser.error("--from must be earlier than --to")

    api_key = os.environ.get("OPENAQ_API_KEY", "")
    if args.sensor_id is not None:
        snapshot = download_hourly(
            args.sensor_id, args.datetime_from, args.datetime_to, api_key, args.limit
        )
    else:
        latitude, longitude = args.coordinates or DEFAULT_BENGALURU_COORDINATES
        sensors = discover_pm25_sensors(latitude, longitude, args.radius, api_key, args.limit)
        snapshot = download_sensors_hourly(
            sensors, args.datetime_from, args.datetime_to, api_key, args.limit
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    temporary.replace(args.output)
    print(f"Saved {len(snapshot['results'])} hourly measurements to {args.output}")


if __name__ == "__main__":
    main()
