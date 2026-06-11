from __future__ import annotations

from typing import Any

from ..stores.sqlite import SQLiteMemoryStore
from ..types import EmbeddingProvider, VectorStore


class VectorSeedRetriever:
    def __init__(self, *, vector_store: VectorStore, embedding_provider: EmbeddingProvider):
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider

    def retrieve(
        self,
        *,
        profile_id: str,
        query: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        vector = self.embedding_provider.embed(query)
        return [
            {**item, "source": item.get("source", "vector")}
            for item in self.vector_store.search(
                vector,
                profile_id=profile_id,
                limit=max(limit * 2, 8),
                filters=filters or {},
            )
        ]


class FtsSeedRetriever:
    def __init__(self, store: SQLiteMemoryStore):
        self.store = store

    def retrieve(self, *, profile_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        return self.store.search_fts(
            profile_id=profile_id,
            query=query,
            limit=max(limit, 8),
        )


class EntitySeedRetriever:
    def __init__(self, store: SQLiteMemoryStore):
        self.store = store

    def retrieve(
        self,
        *,
        profile_id: str,
        entity_names: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.store.search_entities(
            profile_id=profile_id,
            names=[
                str(value)
                for value in entity_names
                if str(value).strip()
            ],
            limit=max(limit, 8),
        )
