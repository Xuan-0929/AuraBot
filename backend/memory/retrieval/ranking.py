from __future__ import annotations

from typing import Any

from ..stores.sqlite import SQLiteMemoryStore


def hydrate_and_rank_memories(
    *,
    store: SQLiteMemoryStore,
    profile_id: str,
    fused_candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    memories = store.get_active_memories(
        [item["memory_id"] for item in fused_candidates],
        profile_id,
    )
    score_by_id = {
        item["memory_id"]: item["score"]
        for item in fused_candidates
    }
    source_by_id = {
        item["memory_id"]: item["source"]
        for item in fused_candidates
    }

    return [
        {
            **memory,
            "score": score_by_id[memory["memory_id"]],
            "recall_source": source_by_id[memory["memory_id"]],
        }
        for memory in memories
    ][:limit]


def filter_relations_for_ranked_memories(
    *,
    relations: list[dict[str, Any]],
    ranked_memories: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    returned_ids = {
        memory["memory_id"]
        for memory in ranked_memories
    }

    return [
        relation
        for relation in relations
        if relation["source_id"] in returned_ids or relation["target_id"] in returned_ids
    ]
