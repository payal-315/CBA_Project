import json

from fastavro import writer

from src.ingestion import read_file


def test_reads_json_array(tmp_path):
    source = tmp_path / "readings.json"
    source.write_text(json.dumps([{"sensor_id": "A", "timestamp": "2026-01-01T00:00:00Z"}]))
    assert read_file(source)[0]["sensor_id"] == "A"


def test_reads_openaq_hourly_response_and_groups_parameters(tmp_path):
    source = tmp_path / "openaq.json"
    source.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "sensorsId": 123,
                        "period": {"datetimeFrom": {"utc": "2026-01-01T00:00:00Z"}},
                        "parameter": {"name": "pm2.5", "units": "µg/m³"},
                        "value": 12.5,
                    },
                    {
                        "sensorsId": 123,
                        "period": {"datetimeFrom": {"utc": "2026-01-01T00:00:00Z"}},
                        "parameter": {"name": "temperature", "units": "c"},
                        "value": 22,
                    },
                ]
            }
        )
    )
    result = read_file(source)
    assert len(result) == 1
    assert result[0]["sensor_id"] == "123"
    assert result[0]["temperature"] == 22
    assert result[0]["air_quality"]["pm25"] == 12.5


def test_reads_avro_records(tmp_path):
    source = tmp_path / "readings.avro"
    schema = {
        "type": "record",
        "name": "Reading",
        "fields": [
            {"name": "sensor_id", "type": "string"},
            {"name": "timestamp", "type": "string"},
            {"name": "temperature", "type": ["null", "double"], "default": None},
        ],
    }
    with source.open("wb") as file_obj:
        writer(file_obj, schema, [{"sensor_id": "A", "timestamp": "2026-01-01T00:00:00Z", "temperature": 21.5}])
    assert read_file(source)[0]["temperature"] == 21.5
