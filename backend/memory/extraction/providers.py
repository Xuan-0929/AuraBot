from __future__ import annotations

from typing import Any, Protocol


class MemoryExtractionModel(Protocol):
    def extract_memory_payload(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        ...


class StaticExtractionModel:
    """Small test/offline provider that returns a fixed extraction payload."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def extract_memory_payload(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> dict[str, Any]:
        return self.payload
