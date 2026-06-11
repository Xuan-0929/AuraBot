from __future__ import annotations

from typing import Any

from .consolidation.strategies import (
    ConflictingPreferenceStrategy,
    DuplicateMemoryStrategy,
)
from .stores.sqlite import SQLiteMemoryStore


class AddFirstConsolidator:
    """Mem0-inspired ADD-first consolidator.

    The hot write path never overwrites memory. Background improve can mark exact
    duplicates stale while preserving provenance.
    """

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        duplicate_strategy: DuplicateMemoryStrategy | None = None,
        conflict_strategy: ConflictingPreferenceStrategy | None = None,
    ):
        self.store = store
        self.duplicate_strategy = duplicate_strategy or DuplicateMemoryStrategy()
        self.conflict_strategy = conflict_strategy or ConflictingPreferenceStrategy()

    def improve(self, profile_id: str) -> dict[str, Any]:
        memories = self.store.list_active_memories(profile_id)
        duplicate_plan = self.duplicate_strategy.select(memories)
        conflict_plan = self.conflict_strategy.select(memories)
        superseded_ids = conflict_plan.superseded_ids
        stale_ids = [
            memory_id
            for memory_id in duplicate_plan.stale_ids
            if memory_id not in set(superseded_ids)
        ]
        stale_count = self.store.mark_memories_stale(
            profile_id=profile_id,
            memory_ids=stale_ids,
        )
        superseded_count = self.store.mark_memories_superseded(
            profile_id=profile_id,
            memory_ids=superseded_ids,
        )
        result = {
            "profile_id": profile_id,
            "stale_count": stale_count,
            "superseded_count": superseded_count,
            "merged_count": 0,
            "stale_ids": stale_ids,
            "superseded_ids": superseded_ids,
        }
        self.store.write_audit_log(
            profile_id=profile_id,
            action="improve",
            input_summary="background consolidation",
            result_json=result,
        )
        return result
