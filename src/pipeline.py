"""Command-line entry point for the batch sensor data pipeline."""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

from .cleaning import clean_and_validate
from .ingestion import read_files
from .mongodb_loader import load_documents
from .transformation import dataframe_to_documents
from .weather_enrichment import enrich_temperatures

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__} to JSON")


def run_pipeline(
    inputs: list[Path],
    processed_path: Path,
    rejected_path: Path,
    report_path: Path,
    rolling_window: int = 3,
    load_mongodb: bool = False,
    add_weather_temperature: bool = False,
) -> dict[str, Any]:
    if not inputs:
        raise ValueError("No input files were found")
    records = read_files(inputs)
    weather_counts = {"weather_temperatures_added": 0, "weather_temperatures_unavailable": 0}
    if add_weather_temperature:
        records, weather_counts = enrich_temperatures(
            records,
            os.environ.get("OPENAQ_API_KEY", ""),
        )
    cleaned, rejected, report = clean_and_validate(records, rolling_window=rolling_window)
    documents = dataframe_to_documents(cleaned)

    processed_path.parent.mkdir(parents=True, exist_ok=True)
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    processed_path.write_text(json.dumps(documents, indent=2, default=_json_default), encoding="utf-8")
    rejected.to_json(rejected_path, orient="records", indent=2, date_format="iso")

    result = report.to_dict()
    result.update(weather_counts)
    result["successfully_stored_observations"] = 0
    if load_mongodb:
        result["successfully_stored_observations"] = load_documents(
            documents,
            os.environ.get("MONGODB_URI", ""),
            os.environ.get("MONGODB_DATABASE", "smart_city"),
        )
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        help="Input JSON/JSON Lines/Avro file or directory (repeatable; default: data/raw)",
    )
    parser.add_argument("--processed-output", type=Path, default=PROJECT_ROOT / "data/processed/readings.json")
    parser.add_argument("--rejected-output", type=Path, default=PROJECT_ROOT / "data/rejected/records.json")
    parser.add_argument("--report-output", type=Path, default=PROJECT_ROOT / "reports/data_quality_report.json")
    parser.add_argument("--rolling-window", type=int, default=3)
    parser.add_argument(
        "--add-weather-temperature",
        action="store_true",
        help="Add hourly model-derived temperatures from OpenAQ station coordinates using Open-Meteo",
    )
    parser.add_argument("--load-mongodb", action="store_true", help="Upsert results into MongoDB")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    targets = args.input or [PROJECT_ROOT / "data/raw"]
    inputs: list[Path] = []
    supported = {".json", ".jsonl", ".ndjson", ".avro"}
    for target in targets:
        if target.is_file():
            inputs.append(target)
        elif target.is_dir():
            inputs.extend(sorted(path for path in target.rglob("*") if path.is_file() and path.suffix.lower() in supported))
        else:
            parser.error(f"Input path does not exist: {target}")
    if not inputs:
        parser.error("No supported input files found")

    result = run_pipeline(
        inputs,
        args.processed_output,
        args.rejected_output,
        args.report_output,
        args.rolling_window,
        args.load_mongodb,
        args.add_weather_temperature,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
