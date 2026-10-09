"""Readers for JSON, JSON Lines, Avro, and OpenAQ v3 measurement responses."""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastavro import reader

logger = logging.getLogger(__name__)


def _json_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: each JSON Lines record must be an object")
            records.append(item)
        return normalize_openaq_records(records)

    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("records", payload.get("results", [payload]))
    else:
        raise ValueError(f"{path}: expected a JSON object or array")

    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError(f"{path}: expected a list of JSON objects")
    return normalize_openaq_records(records)


def normalize_openaq_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAQ's long-form hourly result rows into one row per sensor/hour."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        period = row.get("period") or {}
        period_start = period.get("datetimeFrom") or {}
        datetime_obj = row.get("datetime") or {}
        timestamp = (
            period_start.get("utc")
            or datetime_obj.get("utc")
            or row.get("timestamp")
            or (datetime_obj if isinstance(datetime_obj, str) else None)
        )
        local_timestamp = period_start.get("local") or (
            datetime_obj.get("local") if isinstance(datetime_obj, dict) else None
        )
        parameter = row.get("parameter") or {}
        sensor_id = (
            row.get("sensorsId")
            or row.get("sensorId")
            or row.get("sensor_id")
            or row.get("sensor")
        )
        if sensor_id is None or timestamp is None or "value" not in row or not parameter:
            # These are already canonical rows or unrelated API envelope objects.
            if "sensor_id" in row and "timestamp" in row:
                grouped[(str(row["sensor_id"]), str(row["timestamp"]))] = row
                continue
            raise ValueError("OpenAQ hourly result is missing sensor, timestamp, parameter, or value")

        sensor_id = str(sensor_id)
        key = (sensor_id, str(timestamp))
        record = grouped.setdefault(
            key,
            {
                "sensor_id": sensor_id,
                "timestamp": timestamp,
                "local_timestamp": local_timestamp,
                "metadata": {},
                "air_quality": {},
            },
        )
        name = str(parameter.get("name") or parameter.get("displayName") or "measurement")
        normalized_name = name.strip().lower().replace(".", "").replace(" ", "_").replace("-", "_")
        if normalized_name in {"temperature", "temp"}:
            record["temperature"] = row["value"]
        else:
            record["air_quality"][normalized_name] = row["value"]

        metadata = record["metadata"]
        if row.get("locationsId") is not None:
            metadata["location_id"] = row["locationsId"]
        if row.get("location"):
            metadata["location_name"] = row["location"]
        coordinates = row.get("coordinates")
        if isinstance(coordinates, dict):
            metadata["coordinates"] = coordinates
        if parameter.get("units"):
            metadata.setdefault("units", {})[normalized_name] = parameter["units"]

    return list(grouped.values())


def read_file(path: str | Path) -> list[dict[str, Any]]:
    """Read a supported sensor data file and return records in a common row format."""
    source = Path(path)
    suffix = source.suffix.lower()
    logger.info("Reading %s", source)
    if suffix in {".json", ".jsonl", ".ndjson"}:
        return _json_records(source)
    if suffix == ".avro":
        with source.open("rb") as file_obj:
            records = list(reader(file_obj))
        if any(not isinstance(row, dict) for row in records):
            raise ValueError(f"{source}: Avro records must be objects")
        return records
    raise ValueError(f"Unsupported input format for {source}; expected JSON, JSON Lines, or Avro")


def read_files(paths: list[str | Path]) -> list[dict[str, Any]]:
    """Read multiple files, surfacing the filename for any parsing failure."""
    records: list[dict[str, Any]] = []
    for path in paths:
        try:
            records.extend(read_file(path))
        except (OSError, ValueError) as exc:
            logger.error("Failed to ingest %s: %s", path, exc)
            raise
    return records
