"""Optional REST API for reading sensor observations from MongoDB."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from pymongo import MongoClient
from pymongo.collection import Collection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _time_filter(start: datetime, end: datetime) -> dict[str, Any]:
    start_utc = _utc_datetime(start)
    end_utc = _utc_datetime(end)
    if start_utc >= end_utc:
        raise HTTPException(status_code=422, detail="start must be earlier than end")
    return {"timestamp": {"$gte": start_utc, "$lt": end_utc}}


def create_app(collection: Collection[dict[str, Any]] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client: MongoClient[dict[str, Any]] | None = None
        if collection is None:
            mongo_uri = os.environ.get("MONGODB_URI", "")
            if not mongo_uri:
                raise RuntimeError("Set MONGODB_URI to start the MongoDB API")
            client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
            database = os.environ.get("MONGODB_DATABASE", "smart_city")
            name = os.environ.get("MONGODB_COLLECTION", "sensor_readings")
            app.state.collection = client[database][name]
        else:
            app.state.collection = collection
        try:
            yield
        finally:
            if client is not None:
                client.close()

    app = FastAPI(
        title="Smart City Sensor Analytics API",
        description="Read air-quality and modelled-temperature observations from MongoDB.",
        version="1.0.0",
        lifespan=lifespan,
    )

    def get_collection(request: Request) -> Collection[dict[str, Any]]:
        return request.app.state.collection

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        collection_obj = get_collection(request)
        collection_obj.database.client.admin.command("ping")
        return {"status": "ok"}

    @app.get("/sensors")
    def sensors(request: Request) -> dict[str, list[str]]:
        sensor_ids = get_collection(request).distinct("metadata.sensor_id")
        return {"sensor_ids": sorted(str(sensor_id) for sensor_id in sensor_ids)}

    @app.get("/sensors/{sensor_id}/readings")
    def readings(
        sensor_id: str,
        request: Request,
        start: datetime | None = Query(default=None),
        end: datetime | None = Query(default=None),
        limit: int = Query(default=500, ge=1, le=5000),
    ) -> dict[str, Any]:
        if (start is None) != (end is None):
            raise HTTPException(status_code=422, detail="start and end must be provided together")
        query: dict[str, Any] = {"metadata.sensor_id": sensor_id}
        if start is not None and end is not None:
            query.update(_time_filter(start, end))
        documents = list(
            get_collection(request)
            .find(query, {"_id": 0})
            .sort("timestamp", 1)
            .limit(limit)
        )
        return {"sensor_id": sensor_id, "count": len(documents), "readings": documents}

    @app.get("/analytics/temperature")
    def temperature_summary(
        request: Request,
        start: datetime = Query(...),
        end: datetime = Query(...),
    ) -> dict[str, Any]:
        match = _time_filter(start, end)
        pipeline = [
            {"$match": {"measurements.temperature": {"$exists": True}, **match}},
            {
                "$group": {
                    "_id": "$metadata.sensor_id",
                    "readings": {"$sum": 1},
                    "average": {"$avg": "$measurements.temperature"},
                    "minimum": {"$min": "$measurements.temperature"},
                    "maximum": {"$max": "$measurements.temperature"},
                    "modelled_readings": {
                        "$sum": {
                            "$cond": ["$data_quality.temperature_is_modelled", 1, 0]
                        }
                    },
                    "imputed_readings": {
                        "$sum": {"$cond": ["$data_quality.temperature_imputed", 1, 0]}
                    },
                }
            },
            {"$sort": {"_id": 1}},
        ]
        results = list(get_collection(request).aggregate(pipeline))
        return {"start": _utc_datetime(start), "end": _utc_datetime(end), "sensors": results}

    @app.get("/analytics/air-quality")
    def air_quality_summary(
        request: Request,
        parameter: str = Query(pattern="^(pm25|pm10|ozone|aqi)$"),
        start: datetime = Query(...),
        end: datetime = Query(...),
    ) -> dict[str, Any]:
        match = _time_filter(start, end)
        field = f"$measurements.air_quality.{parameter}"
        pipeline = [
            {"$match": {f"measurements.air_quality.{parameter}": {"$exists": True}, **match}},
            {
                "$group": {
                    "_id": "$metadata.sensor_id",
                    "readings": {"$sum": 1},
                    "average": {"$avg": field},
                    "minimum": {"$min": field},
                    "maximum": {"$max": field},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        results = list(get_collection(request).aggregate(pipeline))
        return {
            "parameter": parameter,
            "start": _utc_datetime(start),
            "end": _utc_datetime(end),
            "sensors": results,
        }

    return app


app = create_app()
