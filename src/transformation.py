"""Convert cleaned tabular rows into MongoDB-ready sensor observation documents."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

import pandas as pd

from .cleaning import BASE_MEASUREMENTS


def _present(value: Any) -> bool:
    if value is None:
        return False
    try:
        return not bool(pd.isna(value))
    except (TypeError, ValueError):
        return True


def dataframe_to_documents(frame: pd.DataFrame) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        sensor_id = str(row["sensor_id"])
        timestamp = row["timestamp"]
        if not isinstance(timestamp, datetime):
            timestamp = pd.Timestamp(timestamp).to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=pd.Timestamp.utcnow().tzinfo)

        metadata = row.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["sensor_id"] = sensor_id
        for field in ("location", "coordinates", "model", "location_id", "location_name"):
            value = row.get(field)
            if _present(value):
                metadata[field] = value

        measurements: dict[str, Any] = {}
        if _present(row.get("temperature")):
            measurements["temperature"] = float(row["temperature"])
        air_quality: dict[str, Any] = {}
        for field in BASE_MEASUREMENTS:
            if field != "temperature" and _present(row.get(field)):
                air_quality[field] = float(row[field])
        reserved = {
            "sensor_id", "timestamp", "local_timestamp", "metadata", "location", "coordinates", "model",
            "location_id", "location_name", "temperature_imputed", "temperature_source",
            "temperature_is_modelled", "validated",
            "rejection_reason", *BASE_MEASUREMENTS,
        }
        for field, value in row.items():
            if field not in reserved and _present(value):
                air_quality[field] = float(value)
        if air_quality:
            measurements["air_quality"] = air_quality

        identity = f"{sensor_id}|{timestamp.isoformat()}"
        documents.append(
            {
                "_id": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "timestamp": timestamp,
                "metadata": metadata,
                "measurements": measurements,
                "data_quality": {
                    "temperature_imputed": bool(row.get("temperature_imputed", False)),
                    "validated": True,
                    "temperature_source": row.get("temperature_source"),
                    "temperature_is_modelled": bool(row.get("temperature_is_modelled", False)),
                },
            }
        )
    return documents
