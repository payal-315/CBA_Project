from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from api.main import create_app


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents
        self.maximum = None

    def sort(self, _field, _direction):
        return self

    def limit(self, maximum):
        self.maximum = maximum
        return self

    def __iter__(self):
        return iter(self.documents[: self.maximum])


class FakeCollection:
    def __init__(self):
        self.last_query = None

    def distinct(self, field):
        assert field == "metadata.sensor_id"
        return ["SEN002", "SEN001"]

    def find(self, query, projection):
        self.last_query = (query, projection)
        return FakeCursor(
            [
                {
                    "timestamp": datetime(2026, 10, 1, tzinfo=timezone.utc),
                    "measurements": {"temperature": 20.0},
                }
            ]
        )

    def aggregate(self, pipeline):
        self.last_query = pipeline
        return [
            {
                "_id": "SEN001",
                "readings": 1,
                "average": 20.0,
                "minimum": 20.0,
                "maximum": 20.0,
            }
        ]


@pytest.fixture
def collection():
    return FakeCollection()


def test_lists_sensors_sorted(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get("/sensors")
    assert response.status_code == 200
    assert response.json() == {"sensor_ids": ["SEN001", "SEN002"]}


def test_filters_readings_by_sensor_and_time(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get(
            "/sensors/SEN001/readings",
            params={"start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z"},
        )
    assert response.status_code == 200
    query, _ = collection.last_query
    assert query["metadata.sensor_id"] == "SEN001"
    assert query["timestamp"]["$gte"] == datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_requires_both_reading_range_bounds(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get(
            "/sensors/SEN001/readings",
            params={"start": "2026-10-01T00:00:00Z"},
        )
    assert response.status_code == 422


def test_rejects_invalid_temperature_range(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get(
            "/analytics/temperature",
            params={"start": "2026-10-02T00:00:00Z", "end": "2026-10-01T00:00:00Z"},
        )
    assert response.status_code == 422


def test_summarizes_air_quality_per_sensor(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get(
            "/analytics/air-quality",
            params={
                "parameter": "pm25",
                "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-02T00:00:00Z",
            },
        )
    assert response.status_code == 200
    assert response.json()["parameter"] == "pm25"
    match = collection.last_query[0]["$match"]
    assert match["measurements.air_quality.pm25"] == {"$exists": True}


def test_rejects_unavailable_air_quality_parameter(collection):
    with TestClient(create_app(collection)) as client:
        response = client.get(
            "/analytics/air-quality",
            params={
                "parameter": "humidity",
                "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-02T00:00:00Z",
            },
        )
    assert response.status_code == 422
