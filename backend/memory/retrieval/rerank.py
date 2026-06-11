from __future__ import annotations

from typing import Any, Protocol


class MemoryReranker(Protocol):
    def rerank(
        self,
        *,
        query: str,
        memories: list[dict[str, Any]],
        analysis: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        ...


class RuleBasedMemoryReranker:
    def rerank(
        self,
        *,
        query: str,
        memories: list[dict[str, Any]],
        analysis: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        scored = [
            {
                **memory,
                "rerank_score": rerank_score(
                    query=query,
                    memory=memory,
                    analysis=analysis,
                ),
            }
            for memory in memories
        ]

        return sorted(
            scored,
            key=lambda memory: memory["rerank_score"],
            reverse=True,
        )[:limit]


def rerank_score(*, query: str, memory: dict[str, Any], analysis: dict[str, Any]) -> float:
    score = float(memory.get("score", 0))
    content = str(memory.get("content", ""))
    source = memory.get("recall_source")

    for entity in analysis.get("entities", []):
        if entity and entity in content:
            score += 0.22

    if analysis.get("time", {}).get("valid_at") and memory.get("valid_from"):
        score += 0.18

    if source == "entity":
        score += 0.08
    elif source == "fts":
        score += 0.04

    for token in query.replace("，", " ").replace("。", " ").split():
        if token and token in content:
            score += 0.03

    return score
