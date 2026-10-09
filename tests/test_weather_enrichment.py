from datetime import datetime, timezone

from src.cleaning import clean_and_validate
from src.transformation import dataframe_to_documents
from src.weather_enrichment import TEMPERATURE_SOURCE, enrich_temperatures


def test_adds_hourly_model_temperature_by_station_local_hour(monkeypatch):
    requests = []

    def fake_get_json(url, headers=None):
        requests.append((url, headers))
        if "/locations/" in url:
            return {
                "results": [
                    {
                        "name": "Bengaluru monitor",
                        "timezone": "Asia/Kolkata",
                        "coordinates": {"latitude": 12.9, "longitude": 77.6},
                    }
                ]
            }
        return {
            "hourly": {
                "time": ["2026-10-02T05:00", "2026-10-02T06:00"],
                "temperature_2m": [24.5, 25.0],
            }
        }

    monkeypatch.setattr("src.weather_enrichment._get_json", fake_get_json)
    records = [
        {
            "sensor_id": "S1",
            "timestamp": "2026-10-01T23:30:00Z",
            "local_timestamp": "2026-10-02T05:00:00+05:30",
            "metadata": {"location_id": 5548},
            "air_quality": {"pm25": 20},
        },
        {
            "sensor_id": "S1",
            "timestamp": "2026-10-02T00:30:00Z",
            "local_timestamp": "2026-10-02T06:00:00+05:30",
            "metadata": {"location_id": 5548},
            "air_quality": {"pm25": 21},
        },
    ]
    enriched, counts = enrich_temperatures(records, "test-key")
    assert [row["temperature"] for row in enriched] == [24.5, 25.0]
    assert counts == {"weather_temperatures_added": 2, "weather_temperatures_unavailable": 0}
    assert len(requests) == 2
    assert enriched[0]["metadata"]["coordinates"] == {"latitude": 12.9, "longitude": 77.6}
    assert enriched[0]["temperature_source"] == TEMPERATURE_SOURCE


def test_preserves_weather_provenance_in_mongodb_document():
    records = [
        {
            "sensor_id": "S1",
            "timestamp": "2026-10-01T23:30:00Z",
            "local_timestamp": "2026-10-02T05:00:00+05:30",
            "temperature": 24.5,
            "temperature_source": TEMPERATURE_SOURCE,
            "temperature_is_modelled": True,
            "metadata": {"location_id": 5548},
            "air_quality": {"pm25": 20},
        }
    ]
    cleaned, rejected, report = clean_and_validate(records)
    document = dataframe_to_documents(cleaned)[0]
    assert rejected.empty
    assert report.unresolved_temperature_values == 0
    assert "local_timestamp" not in document["metadata"].get("attributes", {})
    assert document["timestamp"] == datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc)
    assert document["measurements"]["temperature"] == 24.5
    assert document["data_quality"]["temperature_is_modelled"]
    assert document["data_quality"]["temperature_source"] == TEMPERATURE_SOURCE
