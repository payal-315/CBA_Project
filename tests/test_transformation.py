from src.cleaning import clean_and_validate
from src.transformation import dataframe_to_documents


def test_transforms_to_nested_document_with_stable_identity():
    records = [
        {
            "sensor_id": "A",
            "timestamp": "2026-01-01T00:00:00Z",
            "temperature": 20,
            "pm25": 10,
            "metadata": {"model": "M1"},
        }
    ]
    cleaned, _, _ = clean_and_validate(records)
    first = dataframe_to_documents(cleaned)[0]
    second = dataframe_to_documents(cleaned)[0]
    assert first["_id"] == second["_id"]
    assert first["metadata"]["sensor_id"] == "A"
    assert first["measurements"]["air_quality"]["pm25"] == 10
    assert first["data_quality"]["validated"]
