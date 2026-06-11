from __future__ import annotations

from typing import Any


def fuse_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fused: dict[str, dict[str, Any]] = {}
    source_priority = {"entity": 4, "fts": 3, "vector": 2, "relation": 1}

    for candidate in candidates:
        memory_id = candidate.get("memory_id")
        if not memory_id:
            continue

        score = float(candidate.get("score", 0))
        source = candidate.get("source", "vector")

        if memory_id not in fused:
            fused[memory_id] = {"memory_id": memory_id, "score": score, "source": source}
            continue

        fused[memory_id]["score"] += score * 0.35
        if source_priority.get(source, 0) > source_priority.get(fused[memory_id]["source"], 0):
            fused[memory_id]["source"] = source

    return sorted(fused.values(), key=lambda item: item["score"], reverse=True)
