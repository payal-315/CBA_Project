"""Schema normalization, validation, deduplication, and temperature imputation."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import pandas as pd

logger = logging.getLogger(__name__)

ALIASES = {
    "sensorId": "sensor_id",
    "sensorsId": "sensor_id",
    "sensor": "sensor_id",
    "date_time": "timestamp",
    "datetime": "timestamp",
    "time": "timestamp",
    "temp": "temperature",
    "pm2_5": "pm25",
    "pm2.5": "pm25",
    "pm_25": "pm25",
    "pm10": "pm10",
}
BASE_MEASUREMENTS = ("temperature", "pm25", "pm10", "ozone", "aqi", "humidity")
QUALITY_FIELDS = {"temperature_source", "temperature_is_modelled"}
RECORD_FIELDS = {
    "sensor_id", "timestamp", "local_timestamp", "metadata", "location",
    "coordinates", "air_quality", "measurements",
}


@dataclass
class QualityReport:
    input_records: int = 0
    valid_records: int = 0
    duplicate_records_removed: int = 0
    temperature_values_imputed: int = 0
    unresolved_temperature_values: int = 0
    rejected_records: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def _flatten_record(record: dict[str, Any]) -> dict[str, Any]:
    source = dict(record)
    row: dict[str, Any] = {}
    for canonical, names in (
        ("sensor_id", ("sensor_id", "sensorId", "sensorsId", "sensor")),
        ("timestamp", ("timestamp", "date_time", "datetime", "time")),
        ("local_timestamp", ("local_timestamp",)),
    ):
        for name in names:
            if name in source:
                row[canonical] = source[name]
                break

    metadata = source.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    measurement_values: dict[str, Any] = {}
    nested_measurements = source.get("measurements")
    if isinstance(nested_measurements, dict):
        measurement_values.update(
            {key: value for key, value in nested_measurements.items() if key != "air_quality"}
        )
        nested_air_quality = nested_measurements.get("air_quality")
        if isinstance(nested_air_quality, dict):
            measurement_values.update(nested_air_quality)
    nested_air_quality = source.get("air_quality")
    if isinstance(nested_air_quality, dict):
        measurement_values.update(nested_air_quality)
    for field in BASE_MEASUREMENTS:
        if field in source:
            measurement_values[field] = source[field]
    for old_name, new_name in ALIASES.items():
        if old_name in source and new_name in BASE_MEASUREMENTS:
            measurement_values[new_name] = source[old_name]
        if old_name in measurement_values:
            measurement_values.setdefault(new_name, measurement_values.pop(old_name))
    row.update(measurement_values)
    for field in QUALITY_FIELDS:
        if field in source:
            row[field] = source[field]

    for field in ("location", "coordinates", "model", "latitude", "longitude"):
        if field in source:
            metadata.setdefault(field, source[field])
    known_fields = {
        "sensor_id", "sensorId", "sensorsId", "sensor", "timestamp", "local_timestamp", "date_time",
        "datetime", "time", "metadata", "measurements", "air_quality",
        *BASE_MEASUREMENTS, *ALIASES.keys(), *QUALITY_FIELDS,
    }
    extras = {key: value for key, value in source.items() if key not in known_fields}
    if extras:
        metadata.setdefault("attributes", {}).update(extras)
    row["metadata"] = metadata
    return row


def _coordinate_values(row: pd.Series) -> tuple[Any, Any]:
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return None, None
    location = metadata.get("location")
    coordinates = metadata.get("coordinates")
    candidates = [coordinates, location, metadata]
    for candidate in candidates:
        if isinstance(candidate, dict):
            latitude = candidate.get("latitude", candidate.get("lat"))
            longitude = candidate.get("longitude", candidate.get("lon", candidate.get("lng")))
            if latitude is not None or longitude is not None:
                return latitude, longitude
    return None, None


def clean_and_validate(
    records: list[dict[str, Any]],
    rolling_window: int = 3,
) -> tuple[pd.DataFrame, pd.DataFrame, QualityReport]:
    """Return valid deduplicated records, rejected rows with reasons, and quality counts.

    Temperature gaps are estimated from up to ``rolling_window`` adjacent observations
    for the same sensor. The centered window is intended for historical batch analysis.
    """
    if rolling_window < 1:
        raise ValueError("rolling_window must be at least 1")
    report = QualityReport(input_records=len(records))
    if not records:
        return pd.DataFrame(), pd.DataFrame(columns=["rejection_reason"]), report

    frame = pd.DataFrame([_flatten_record(record) for record in records])
    for field in ("sensor_id", "timestamp", *BASE_MEASUREMENTS, "metadata", "location", "coordinates"):
        if field not in frame:
            frame[field] = None
    frame["sensor_id"] = frame["sensor_id"].astype("string").str.strip()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], format="mixed", utc=True, errors="coerce")

    measurement_columns = list(dict.fromkeys((*BASE_MEASUREMENTS, *(
        column for column in frame.columns
        if column not in RECORD_FIELDS
        and column not in QUALITY_FIELDS
        and column not in {"timestamp", "sensor_id", "local_timestamp"}
    ))))
    invalid_reasons: list[list[str]] = [[] for _ in range(len(frame))]
    for position, (_, row) in enumerate(frame.iterrows()):
        if pd.isna(row["sensor_id"]) or not str(row["sensor_id"]).strip():
            invalid_reasons[position].append("missing sensor_id")
        if pd.isna(row["timestamp"]):
            invalid_reasons[position].append("invalid or missing timestamp")

    for column in measurement_columns:
        original = frame[column]
        converted = pd.to_numeric(original, errors="coerce")
        invalid_value = original.notna() & converted.isna()
        for position in invalid_value[invalid_value].index:
            invalid_reasons[frame.index.get_loc(position)].append(f"non-numeric {column}")
        frame[column] = converted

    for position, (_, row) in enumerate(frame.iterrows()):
        latitude, longitude = _coordinate_values(row)
        for name, value, lower, upper in (
            ("latitude", latitude, -90, 90),
            ("longitude", longitude, -180, 180),
        ):
            if value is None:
                continue
            numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
            if pd.isna(numeric) or not lower <= numeric <= upper:
                invalid_reasons[position].append(f"{name} outside valid range")

    no_measurement = frame[list(measurement_columns)].notna().sum(axis=1).eq(0)
    for position in no_measurement[no_measurement].index:
        invalid_reasons[frame.index.get_loc(position)].append("no numeric measurement supplied")

    rejected_mask = pd.Series([bool(reasons) for reasons in invalid_reasons], index=frame.index)
    rejected = frame.loc[rejected_mask].copy()
    rejected["rejection_reason"] = [
        "; ".join(invalid_reasons[frame.index.get_loc(position)])
        for position in rejected.index
    ]
    valid = frame.loc[~rejected_mask].copy()
    report.rejected_records = len(rejected)

    duplicate_mask = valid.duplicated(subset=["sensor_id", "timestamp"], keep="last")
    report.duplicate_records_removed = int(duplicate_mask.sum())
    valid = valid.loc[~duplicate_mask].copy()
    valid.sort_values(["sensor_id", "timestamp"], inplace=True)
    valid.reset_index(drop=True, inplace=True)

    valid["temperature_imputed"] = False
    if not valid.empty:
        rolling_means = valid.groupby("sensor_id", sort=False)["temperature"].transform(
            lambda values: values.rolling(rolling_window, center=True, min_periods=1).mean()
        )
        missing_temperature = valid["temperature"].isna()
        valid.loc[missing_temperature, "temperature"] = rolling_means[missing_temperature]
        imputed = missing_temperature & valid["temperature"].notna()
        valid.loc[imputed, "temperature_imputed"] = True
        report.temperature_values_imputed = int(imputed.sum())
        report.unresolved_temperature_values = int(valid["temperature"].isna().sum())
    report.valid_records = len(valid)
    logger.info(
        "Validated %d/%d records; removed %d duplicates and imputed %d temperatures",
        report.valid_records,
        report.input_records,
        report.duplicate_records_removed,
        report.temperature_values_imputed,
    )
    return valid, rejected, report
