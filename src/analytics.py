"""Small in-memory summaries for cleaned sensor observations."""

from __future__ import annotations

from typing import Any

import pandas as pd


def summarize_by_sensor(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Return counts and available measurement means grouped by sensor."""
    if frame.empty:
        return []
    measurements = [
        field for field in ("temperature", "pm25", "pm10", "ozone", "aqi", "humidity")
        if field in frame.columns
    ]
    summary = frame.groupby("sensor_id", dropna=False).agg(
        readings=("timestamp", "count"),
        **{f"average_{field}": (field, "mean") for field in measurements},
    )
    return summary.reset_index().to_dict(orient="records")
