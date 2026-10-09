import json
from urllib.parse import parse_qs, urlparse

from src import openaq_download


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_downloads_all_pages_and_uses_api_key_header(monkeypatch):
    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        page = int(parse_qs(urlparse(request.full_url).query)["page"][0])
        return FakeResponse(
            {
                "meta": {"found": 2},
                "results": [{"page": page}],
            }
        )

    monkeypatch.setattr(openaq_download, "urlopen", fake_urlopen)
    result = openaq_download.download_hourly(
        123, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z", "test-key", limit=1
    )
    assert len(result["results"]) == 2
    assert len(requests) == 2
    assert requests[0][0].get_header("X-api-key") == "test-key"
    assert requests[1][1] == 30


def test_discovers_only_pm25_sensors_near_coordinates(monkeypatch):
    captured = []

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return FakeResponse(
            {
                "meta": {"found": 1},
                "results": [
                    {
                        "id": 10,
                        "name": "City monitor",
                        "coordinates": {"latitude": 12.97, "longitude": 77.59},
                        "sensors": [
                            {"id": 201, "parameter": {"id": 2, "name": "pm25"}},
                            {"id": 202, "parameter": {"id": 1, "name": "pm10"}},
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(openaq_download, "urlopen", fake_urlopen)
    sensors = openaq_download.discover_pm25_sensors(12.97, 77.59, 25_000, "test-key")
    assert [sensor["id"] for sensor in sensors] == [201]
    query = parse_qs(urlparse(captured[0][0].full_url).query)
    assert query["parameters_id"] == ["2"]
    assert query["coordinates"] == ["12.97,77.59"]


def test_sensor_download_adds_sensor_identity_to_measurements(monkeypatch):
    monkeypatch.setattr(
        openaq_download,
        "download_hourly",
        lambda *_args, **_kwargs: {
            "results": [
                {
                    "value": 12.5,
                    "parameter": {"name": "pm25"},
                    "period": {"datetimeFrom": {"utc": "2026-01-01T00:00:00Z"}},
                }
            ]
        },
    )
    monkeypatch.setattr(openaq_download.time, "sleep", lambda *_args: None)
    result = openaq_download.download_sensors_hourly(
        [{"id": 123, "location_id": 45, "location_name": "Station"}],
        "2026-01-01T00:00:00Z",
        "2026-01-02T00:00:00Z",
        "test-key",
    )
    row = result["results"][0]
    assert row["sensorsId"] == 123
    assert row["locationsId"] == 45
