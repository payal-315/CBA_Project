# Smart City IoT Weather Sensor Analytics

A batch data engineering pipeline for hourly environmental readings. It ingests local JSON, JSON Lines, and Avro files; normalizes and validates records with Pandas; removes repeated sensor/timestamp observations; estimates eligible missing temperatures; and writes nested MongoDB-ready documents and a quality report.

OpenAQ is supported as a real air-quality source. Its measurements are parameter-specific and temperature is available only where a sensor reports it. An optional Open-Meteo Historical Forecast API integration adds hourly, model-derived temperature using each OpenAQ station's coordinates and local timezone. These values are a weather-model product, not direct readings from the municipal air-quality sensor, and are explicitly marked with provenance in each document. The included sample file is synthetic test data, not a claim of live OpenAQ measurements.

## Requirements

- Python 3.10+
- MongoDB is optional for local pipeline runs and required only with `--load-mongodb`.
- An OpenAQ API key is required only for downloads. Create one through [OpenAQ](https://explore.openaq.org/register).

## Setup

```powershell
cd C:\path\to\CBA_Project
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a project-root `.env` file with the settings needed for your run. Both command-line programs load it automatically. Keep that file private; all `.env` files are excluded from Git.

```dotenv
OPENAQ_API_KEY=your-openaq-key
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=smart_city
MONGODB_COLLECTION=sensor_readings
```

## Download an OpenAQ hourly JSON snapshot

OpenAQ v3 exposes hourly aggregates at `/v3/sensors/{sensor_id}/hours`, paginates up to 1,000 results per page, and authenticates with `X-API-Key`. By default, this discovers PM2.5 sensors within 25 km of Bengaluru (the sample dataset's location), downloads a UTC date range, and saves a local JSON snapshot:

```powershell
python -m src.openaq_download `
  --from 2026-10-02T00:00:00Z `
  --to 2026-10-09T00:00:00Z
```

Set a different area with `--coordinates LATITUDE LONGITUDE --radius METERS`, or fetch one known sensor with `--sensor-id ID`. The snapshot is retained locally so later runs do not depend on network/API availability. OpenAQ docs: [hourly measurements](https://docs.openaq.org/resources/measurements), [locations](https://docs.openaq.org/resources/locations), [API keys](https://docs.openaq.org/using-the-api/api-key), and [pagination](https://docs.openaq.org/using-the-api/pagination).

## Run the batch pipeline

Run the checked-in example batch (includes a duplicate, a missing temperature, and invalid records to exercise quality handling):

```powershell
python -m src.pipeline
```

Or pass one or more files/directories:

```powershell
python -m src.pipeline --input data\raw\json\openaq_sensor_3917.json
python -m src.pipeline --input data\raw\json --input data\raw\avro
```

To add weather temperatures to downloaded OpenAQ rows and persist the enriched output:

```powershell
python -m src.pipeline --input data\raw\json\openaq_hourly.json --add-weather-temperature --load-mongodb
```

The weather enrichment resolves station coordinates and timezone from OpenAQ using `OPENAQ_API_KEY`, then queries Open-Meteo's [Historical Forecast API](https://open-meteo.com/en/docs/historical-forecast-api) for hourly `temperature_2m`. Existing direct sensor temperature readings are left unchanged. The enrichment matches each measurement to the station's local hour; unreturned weather hours remain eligible for the pipeline's separately flagged rolling-average imputation. The quality report includes counts of weather values added and unavailable. Open-Meteo historical forecast values are model-derived estimates; they should not be represented as measured sensor observations.

Outputs:

- `data/processed/readings.json`: nested transformed observations.
- `data/rejected/records.json`: rejected rows and explicit reasons.
- `reports/data_quality_report.json`: input, valid, duplicate, imputed, unresolved, and rejected counts.

Temperature estimates use a centered rolling mean across the configured number of adjacent records for the **same sensor**, with a default window of three. This is for historical batch analysis and may use a future observation; it is not appropriate for a real-time causal estimate. Estimates are flagged in `data_quality.temperature_imputed`. A null remains when no neighboring valid temperature exists. Air-quality metrics are never imputed by this rule.

## Optional MongoDB load

Set `MONGODB_URI` and optionally `MONGODB_DATABASE`, then explicitly enable database writes:

```powershell
$env:MONGODB_URI = "mongodb://localhost:27017"
$env:MONGODB_DATABASE = "smart_city"
python -m src.pipeline --load-mongodb
```

The loader uses a regular `sensor_readings` collection with a stable SHA-256 observation `_id` and a `{metadata.sensor_id, timestamp}` query index. Reprocessing an observation replaces it instead of appending a duplicate. A regular collection is used instead of a MongoDB time-series collection to ensure upsert-based idempotency; the proposal notes that time-series update and unique-index restrictions vary by MongoDB version.

## Optional REST API

Start the FastAPI service after MongoDB is populated:

```powershell
uvicorn api.main:app --reload
```

Interactive API documentation is available at `http://127.0.0.1:8000/docs`. Endpoints include:

- `GET /health`: verify MongoDB connectivity.
- `GET /sensors`: list sensor IDs.
- `GET /sensors/{sensor_id}/readings?start=...&end=...&limit=...`: fetch readings in a UTC time range.
- `GET /analytics/temperature?start=...&end=...`: temperature summaries by sensor, including modelled and imputed counts.
- `GET /analytics/air-quality?parameter=pm25&start=...&end=...`: air-quality summaries by sensor.

Date/time query values should be ISO-8601, preferably with `Z` for UTC. Reading result limits default to 500 and are capped at 5,000.

## Data contract

Each stored document has a BSON UTC `timestamp`, nested `metadata` (including `sensor_id` and optional location/model data), `measurements` (temperature and a flexible `air_quality` object), and `data_quality` flags. Model-derived temperature observations include `temperature_source` and `temperature_is_modelled`; rolling-average estimates are marked separately with `temperature_imputed`. Required input values are a sensor identifier, parseable timestamp, and at least one numeric measurement. Invalid records are rejected; absent optional values remain null or are omitted in transformed output.

OpenAQ hourly rows are grouped by sensor and UTC observation time, preserving the station's local timestamp for weather matching. Their parameter names become measurement names, allowing a sensor's supported pollutant types to vary without changing the core schema. The 500-sensor count describes the proposed municipal deployment; OpenAQ downloads are per sensor and do not promise 500 available sensors for a selected city/date range.

## Tests

```powershell
pytest
```
