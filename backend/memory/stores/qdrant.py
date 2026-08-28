"""Provide the local Qdrant vector index used synchronously by one Profile's ``MemoryService``.

SQLite remains authoritative for Memory state and Profile filtering. This adapter only normalizes vector
payloads, performs local upsert/search/delete operations, and closes its embedded client with the active
Profile lifecycle; it does not own workers, retries, or conversation buffering.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..types import DEFAULT_COLLECTION_NAME, DEFAULT_VECTOR_DIMENSION


class QdrantLocalVectorStore:
    def __init__(
        self,
        *,
        path: str | Path,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        vector_size: int = DEFAULT_VECTOR_DIMENSION,
    ):
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is required for QdrantLocalVectorStore. "
                "Run `uv sync --project backend` from the repository root."
            ) from exc

        self.path = Path(path)
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.client = QdrantClient(path=str(self.path))
        self._closed = False

        if not self.client.collection_exists(collection_name):
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
            )

    def upsert(self, memory_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        from qdrant_client.models import PointStruct

        payload = normalize_payload(payload)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(id=point_id(memory_id), vector=vector, payload=payload)],
        )

    def search(
        self,
        vector: list[float],
        *,
        profile_id: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        valid_at = filters.get("valid_at")
        query_filter = self._build_filter(profile_id, filters)
        query_limit = limit * 4 if valid_at else limit

        if hasattr(self.client, "query_points"):
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=vector,
                query_filter=query_filter,
                limit=query_limit,
            )
            points = response.points
        else:
            points = self.client.search(
                collection_name=self.collection_name,
                query_vector=vector,
                query_filter=query_filter,
                limit=query_limit,
            )

        return [
            {
                "memory_id": (point.payload or {}).get("memory_id"),
                "score": point.score,
                "source": "vector",
            }
            for point in points
            if (point.payload or {}).get("memory_id") and matches_valid_at(point.payload or {}, valid_at)
        ][:limit]

    def delete(self, memory_ids: list[str]) -> None:
        from qdrant_client.models import PointIdsList

        if not memory_ids:
            return

        self.client.delete(
            collection_name=self.collection_name,
            points_selector=PointIdsList(points=[point_id(memory_id) for memory_id in memory_ids]),
        )

    def close(self) -> None:
        if self._closed:
            return

        inner_client = getattr(self.client, "_client", None)
        collections = getattr(inner_client, "collections", {})

        for collection in list(collections.values()):
            collection_close = getattr(collection, "close", None)

            if collection_close:
                collection_close()

        close = getattr(self.client, "close", None)

        if close:
            close()

        inner_close = getattr(inner_client, "close", None)

        if inner_close and not getattr(inner_client, "closed", False):
            inner_close()

        self._closed = True

    def health(self) -> dict[str, Any]:
        info = self.client.get_collection(self.collection_name)

        return {
            "backend": "qdrant-local",
            "path": str(self.path),
            "collection": self.collection_name,
            "vectors": getattr(info, "vectors_count", None),
            "points": getattr(info, "points_count", None),
        }

    def _build_filter(self, profile_id: str, filters: dict[str, Any]):
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = [
            FieldCondition(key="profile_id", match=MatchValue(value=profile_id)),
            FieldCondition(key="state", match=MatchValue(value="active")),
        ]

        for key, value in filters.items():
            if value is None:
                continue

            if key == "valid_at":
                continue

            conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))

        return Filter(must=conditions)


def point_id(memory_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"aurabot-memory:{memory_id}"))


def normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)

    if normalized.get("valid_from"):
        normalized["valid_from_ts"] = iso_to_timestamp(normalized["valid_from"])

    if normalized.get("valid_to"):
        normalized["valid_to_ts"] = iso_to_timestamp(normalized["valid_to"])

    return normalized


def matches_valid_at(payload: dict[str, Any], valid_at: Any) -> bool:
    if valid_at is None:
        return True

    valid_at_ts = iso_to_timestamp(valid_at)
    valid_from_ts = payload.get("valid_from_ts")
    valid_to_ts = payload.get("valid_to_ts")

    if valid_from_ts is not None and valid_from_ts > valid_at_ts:
        return False

    if valid_to_ts is not None and valid_to_ts < valid_at_ts:
        return False

    return True


def iso_to_timestamp(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)

    if not isinstance(value, str):
        raise ValueError(f"Unsupported timestamp value: {value!r}")

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
