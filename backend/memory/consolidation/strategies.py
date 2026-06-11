from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConsolidationPlan:
    stale_ids: list[str] = field(default_factory=list)
    superseded_ids: list[str] = field(default_factory=list)


class DuplicateMemoryStrategy:
    def select(self, memories: list[dict[str, Any]]) -> ConsolidationPlan:
        ordered = sorted(
            memories,
            key=lambda memory: (memory.get("created_at", ""), memory.get("memory_id", "")),
        )
        seen: dict[tuple[str, str], str] = {}
        stale_ids: list[str] = []

        for memory in ordered:
            memory_id = memory.get("memory_id")
            if not memory_id:
                continue

            key = (
                str(memory.get("memory_type", "")),
                normalize_memory_content(str(memory.get("content", ""))),
            )

            if key in seen:
                stale_ids.append(memory_id)
            else:
                seen[key] = memory_id

        return ConsolidationPlan(stale_ids=stale_ids)


class ConflictingPreferenceStrategy:
    def select(self, memories: list[dict[str, Any]]) -> ConsolidationPlan:
        preferences = [
            memory
            for memory in memories
            if memory.get("memory_type") == "preference"
        ]
        by_entity: dict[str, dict[str, list[dict[str, Any]]]] = {}

        for memory in preferences:
            polarity = memory.get("metadata", {}).get("preference_polarity")
            if polarity not in {"positive", "negative"}:
                continue

            for entity in memory.get("metadata", {}).get("entities", []):
                by_entity.setdefault(entity, {}).setdefault(polarity, []).append(memory)

        superseded_ids: list[str] = []

        for groups in by_entity.values():
            if not groups.get("positive") or not groups.get("negative"):
                continue

            conflicting = sorted(
                [*groups["positive"], *groups["negative"]],
                key=lambda memory: (memory.get("created_at", ""), memory.get("memory_id", "")),
            )
            superseded_ids.extend(
                memory["memory_id"]
                for memory in conflicting[:-1]
                if memory.get("memory_id")
            )

        return ConsolidationPlan(superseded_ids=list(dict.fromkeys(superseded_ids)))


def normalize_memory_content(content: str) -> str:
    return re.sub(r"[\s，。,.！!？?]+", "", content).lower()
