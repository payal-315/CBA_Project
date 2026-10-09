from src.cleaning import clean_and_validate


def test_deduplicates_and_imputes_within_sensor_only():
    records = [
        {"sensor_id": "A", "timestamp": "2026-01-01T00:00:00Z", "temperature": 20, "pm25": 10},
        {"sensor_id": "A", "timestamp": "2026-01-01T01:00:00Z", "temperature": None, "pm25": 11},
        {"sensor_id": "A", "timestamp": "2026-01-01T01:00:00+00:00", "temperature": None, "pm25": 12},
        {"sensor_id": "A", "timestamp": "2026-01-01T02:00:00Z", "temperature": 24, "pm25": 13},
        {"sensor_id": "B", "timestamp": "2026-01-01T01:00:00Z", "temperature": None, "pm25": 4},
    ]
    cleaned, rejected, report = clean_and_validate(records)
    assert rejected.empty
    assert report.duplicate_records_removed == 1
    assert report.temperature_values_imputed == 1
    assert report.unresolved_temperature_values == 1
    sensor_a_middle = cleaned[(cleaned.sensor_id == "A") & (cleaned.timestamp.dt.hour == 1)].iloc[0]
    assert sensor_a_middle.temperature == 22
    assert sensor_a_middle.temperature_imputed


def test_rejects_invalid_timestamp_and_non_numeric_measurement():
    records = [
        {"sensor_id": "A", "timestamp": "bad", "temperature": 20},
        {"sensor_id": "B", "timestamp": "2026-01-01T00:00:00Z", "temperature": "bad", "pm25": 1},
    ]
    cleaned, rejected, report = clean_and_validate(records)
    assert cleaned.empty
    assert report.rejected_records == 2
    assert "invalid or missing timestamp" in rejected.iloc[0].rejection_reason
    assert "non-numeric temperature" in rejected.iloc[1].rejection_reason


def test_flattens_nested_measurements_and_metadata():
    cleaned, rejected, _ = clean_and_validate(
        [
            {
                "sensor_id": "A",
                "timestamp": "2026-01-01T00:00:00Z",
                "measurements": {"temperature": 20, "air_quality": {"pm25": 10}},
                "metadata": {"model": "M1"},
            }
        ]
    )
    assert rejected.empty
    assert cleaned.iloc[0].pm25 == 10


def test_preserves_top_level_metadata_and_checks_nested_coordinates():
    cleaned, rejected, _ = clean_and_validate(
        [
            {
                "sensor_id": "A",
                "timestamp": "2026-01-01T00:00:00Z",
                "temperature": 20,
                "model": "M1",
                "location": {"latitude": 95, "longitude": 10},
            }
        ]
    )
    assert cleaned.empty
    assert "latitude outside valid range" in rejected.iloc[0].rejection_reason
