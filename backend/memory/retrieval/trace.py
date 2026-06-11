from __future__ import annotations

from typing import Any


def build_recall_trace(
    *,
    candidates: list[dict[str, Any]],
    relation_candidates: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    pre_rerank_memories: list[dict[str, Any]] | None = None,
    ranked_memories: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "vector_candidates": [
            item["memory_id"] for item in candidates if item.get("source") == "vector"
        ],
        "fts_candidates": [
            item["memory_id"] for item in candidates if item.get("source") == "fts"
        ],
        "entity_candidates": [
            item["memory_id"] for item in candidates if item.get("source") == "entity"
        ],
        "relation_expansions": relations,
        "relation_candidates": relation_candidates,
        "sources_by_memory": {
            memory["memory_id"]: memory["recall_source"] for memory in ranked_memories
        },
        "pre_rerank": [
            memory_trace_item(memory, include_rerank=False)
            for memory in (pre_rerank_memories or ranked_memories)
        ],
        "post_rerank": [
            memory_trace_item(memory, include_rerank=True)
            for memory in ranked_memories
        ],
    }


def memory_trace_item(memory: dict[str, Any], *, include_rerank: bool) -> dict[str, Any]:
    item = {
        "memory_id": memory["memory_id"],
        "score": memory.get("score"),
        "source": memory.get("recall_source"),
        "memory_type": memory.get("memory_type"),
    }

    if include_rerank:
        item["rerank_score"] = memory.get("rerank_score", memory.get("score"))

    return item
