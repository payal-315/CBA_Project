"""Idempotent MongoDB storage using a regular collection and stable observation IDs."""

from __future__ import annotations

import logging
from typing import Any

from pymongo import ASCENDING, MongoClient, ReplaceOne

logger = logging.getLogger(__name__)


def load_documents(
    documents: list[dict[str, Any]],
    mongo_uri: str,
    database_name: str = "smart_city",
    collection_name: str = "sensor_readings",
) -> int:
    """Upsert documents by deterministic ID; return the number processed."""
    if not mongo_uri:
        raise ValueError("A MongoDB URI is required")
    if not documents:
        return 0

    with MongoClient(mongo_uri, serverSelectionTimeoutMS=5000) as client:
        collection = client[database_name][collection_name]
        collection.create_index(
            [("metadata.sensor_id", ASCENDING), ("timestamp", ASCENDING)],
            name="sensor_timestamp",
        )
        operations = [
            ReplaceOne({"_id": document["_id"]}, document, upsert=True)
            for document in documents
        ]
        result = collection.bulk_write(operations, ordered=False)
        stored = result.upserted_count + result.modified_count
        logger.info("Processed %d observations (%d inserted or updated)", len(documents), stored)
        return len(documents)
