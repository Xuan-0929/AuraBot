from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


DEFAULT_VECTOR_DIMENSION = 384
DEFAULT_COLLECTION_NAME = "aurabot_memory_items"


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    memory_type: str = "semantic"
    importance: float = 0.5
    confidence: float = 0.7
    valid_from: str | None = None
    valid_to: str | None = None
    entities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntityCandidate:
    name: str
    entity_type: str = "concept"
    aliases: list[str] = field(default_factory=list)
    summary: str = ""


class EmbeddingProvider(Protocol):
    dimension: int

    def embed(self, text: str) -> list[float]:
        ...


class VectorStore(Protocol):
    def upsert(self, memory_id: str, vector: list[float], payload: dict[str, Any]) -> None:
        ...

    def search(
        self,
        vector: list[float],
        *,
        profile_id: str,
        limit: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def delete(self, memory_ids: list[str]) -> None:
        ...

    def health(self) -> dict[str, Any]:
        ...
