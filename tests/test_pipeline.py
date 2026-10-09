import json

from src.pipeline import run_pipeline


def test_pipeline_writes_processed_rejected_and_quality_outputs(tmp_path):
    source = tmp_path / "input.json"
    source.write_text(
        json.dumps(
            [
                {"sensor_id": "A", "timestamp": "2026-01-01T00:00:00Z", "temperature": 20},
                {"sensor_id": "A", "timestamp": "2026-01-01T01:00:00Z", "temperature": 22},
            ]
        )
    )
    processed, rejected, report = (tmp_path / "out/readings.json", tmp_path / "out/rejected.json", tmp_path / "out/report.json")
    result = run_pipeline([source], processed, rejected, report)
    assert result["valid_records"] == 2
    assert result["successfully_stored_observations"] == 0
    documents = json.loads(processed.read_text())
    assert documents[0]["measurements"]["temperature"] == 20
    assert json.loads(report.read_text())["input_records"] == 2
