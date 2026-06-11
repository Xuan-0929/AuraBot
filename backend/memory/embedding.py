from __future__ import annotations

import hashlib
import math
from typing import Any

from .types import DEFAULT_VECTOR_DIMENSION


def create_embedding_provider(config: dict[str, Any] | None = None):
    config = config or {}
    provider = str(config.get("provider", "hash")).lower()

    if provider == "hash":
        return HashEmbeddingProvider(dimension=int(config.get("dimension", DEFAULT_VECTOR_DIMENSION)))

    raise ValueError(f"Unsupported embedding provider: {provider}")


class HashEmbeddingProvider:
    """Dependency-free embedding provider for tests and offline bootstrapping."""

    def __init__(self, dimension: int = DEFAULT_VECTOR_DIMENSION):
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension

        for token in tokenize(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector

        return [value / norm for value in vector]


def tokenize(text: str) -> list[str]:
    normalized = text.lower().strip()
    tokens = [token for token in normalized.replace("\n", " ").split(" ") if token]

    if len(normalized) <= 96:
        tokens.extend(
            normalized[index : index + 2]
            for index in range(max(len(normalized) - 1, 0))
        )
        tokens.extend(
            normalized[index : index + 3]
            for index in range(max(len(normalized) - 2, 0))
        )

    return tokens or [normalized]
