"""Adapt one process-shared embedded Qdrant collection to AuraBot's vector-store protocol.

Wrappers targeting the same local path share one Qdrant client with a small locked reference count,
because FastAPI test/application compositions may overlap while embedded Qdrant permits only one
process-local file owner. Search/upsert/delete remain synchronous adapter calls used through the
shared asyncio executor by MemoryBridge/MemoryWorker. A condition-protected closing entry remains
registered until the last slow close releases the file lock, fencing a concurrent constructor.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Condition
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..types import DEFAULT_COLLECTION_NAME, DEFAULT_VECTOR_DIMENSION


_CLIENT_CONDITION = Condition()
_LOCAL_CLIENTS: dict[Path, dict[str, Any]] = {}


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

        self.path = Path(path).resolve()
        self.collection_name = collection_name
        self.vector_size = vector_size
        with _CLIENT_CONDITION:
            shared = _LOCAL_CLIENTS.get(self.path)
            while shared is not None and shared["closing"]:
                _CLIENT_CONDITION.wait()
                shared = _LOCAL_CLIENTS.get(self.path)
            if shared is not None:
                if (
                    shared["collection_name"] != collection_name
                    or shared["vector_size"] != vector_size
                ):
                    raise RuntimeError("Qdrant local path is already open with incompatible settings")
                shared["references"] += 1
                self.client = shared["client"]
            else:
                self.client = QdrantClient(path=str(self.path))
                _LOCAL_CLIENTS[self.path] = {
                    "client": self.client,
                    "collection_name": collection_name,
                    "vector_size": vector_size,
                    "references": 1,
                    "closing": False,
                }
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
        client = None
        shared = None
        with _CLIENT_CONDITION:
            if self._closed:
                return
            self._closed = True
            shared = _LOCAL_CLIENTS.get(self.path)
            if shared is not None and shared["client"] is self.client:
                shared["references"] -= 1
                if shared["references"] == 0:
                    shared["closing"] = True
                    client = self.client
        if client is not None:
            close = getattr(client, "close", None)
            try:
                if close:
                    close()
            finally:
                with _CLIENT_CONDITION:
                    if _LOCAL_CLIENTS.get(self.path) is shared:
                        _LOCAL_CLIENTS.pop(self.path, None)
                    _CLIENT_CONDITION.notify_all()

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
