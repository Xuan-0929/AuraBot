from __future__ import annotations

from typing import Any

from ..stores.sqlite import SQLiteMemoryStore


def expand_relation_candidates(
    *,
    store: SQLiteMemoryStore,
    profile_id: str,
    seed_ids: list[str],
    existing_ids: set[str],
    limit: int = 16,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relations = store.list_relations_for_memories(
        profile_id=profile_id,
        memory_ids=seed_ids,
        limit=limit,
    )
    relation_ids = []

    for relation in relations:
        if relation["source_id"] in seed_ids:
            relation_ids.append(relation["target_id"])
        elif relation["target_id"] in seed_ids:
            relation_ids.append(relation["source_id"])

    relation_candidates = [
        {"memory_id": memory_id, "score": 0.48, "source": "relation"}
        for memory_id in relation_ids
        if memory_id not in existing_ids
    ]

    return relation_candidates, relations
