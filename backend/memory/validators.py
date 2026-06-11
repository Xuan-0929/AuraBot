from __future__ import annotations

from .types import MemoryCandidate


def validate_memory_candidate(candidate: MemoryCandidate) -> MemoryCandidate:
    if not candidate.content.strip():
        raise ValueError("memory candidate content cannot be empty")

    if not candidate.memory_type.strip():
        raise ValueError("memory candidate memory_type cannot be empty")

    if not 0 <= candidate.importance <= 1:
        raise ValueError("memory candidate importance must be between 0 and 1")

    if not 0 <= candidate.confidence <= 1:
        raise ValueError("memory candidate confidence must be between 0 and 1")

    return candidate
