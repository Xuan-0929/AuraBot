"""Combine Profile-scoped vector, lexical, entity, relation, and rerank retrieval safely.

SQLite remains the authoritative hydration source. Optional retrieval branches are isolated so a broken
derived vector index or one malformed seed result cannot suppress successful SQLite recall; only sanitized
branch names enter diagnostics. This module directly queries its adapters and packs prompt context once
for MemoryService to return, without seed forwarding wrappers.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from .retrieval.context import pack_context
from .retrieval.fusion import fuse_candidates
from .retrieval.query import QueryAnalyzer
from .retrieval.ranking import filter_relations_for_ranked_memories, hydrate_and_rank_memories
from .retrieval.rerank import MemoryReranker, RuleBasedMemoryReranker
from .retrieval.relations import expand_relation_candidates
from .retrieval.trace import build_recall_trace
from .stores.sqlite import SQLiteMemoryStore
from .types import EmbeddingProvider, VectorStore


class HybridMemoryRetriever:
    def __init__(
        self,
        *,
        store: SQLiteMemoryStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        reranker: MemoryReranker | None = None,
    ):
        self.store = store
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.query_analyzer = QueryAnalyzer()
        self.reranker = reranker or RuleBasedMemoryReranker()

    def recall(
        self,
        *,
        profile_id: str,
        query: str,
        limit: int,
        filters: dict[str, Any] | None = None,
        context_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filters = filters or {}
        context_hints = context_hints or {}
        analysis = self.query_analyzer.analyze(
            query=query,
            context_hints=context_hints,
        )
        retrieval_filters = dict(filters)
        if analysis["time"].get("valid_at") and "valid_at" not in retrieval_filters:
            retrieval_filters["valid_at"] = analysis["time"]["valid_at"]

        failed_branches: list[str] = []
        candidates: list[dict[str, Any]] = []
        candidates.extend(_safe_seeds(
            "vector",
            lambda: [
                {**item, "source": item.get("source", "vector")}
                for item in self.vector_store.search(
                    self.embedding_provider.embed(query),
                    profile_id=profile_id,
                    limit=max(limit * 2, 8),
                    filters=retrieval_filters,
                )
            ],
            failed_branches,
        ))
        candidates.extend(_safe_seeds(
            "fts",
            lambda: self.store.search_fts(
                profile_id=profile_id,
                query=query,
                limit=max(limit, 8),
            ),
            failed_branches,
        ))
        candidates.extend(_safe_seeds(
            "entity",
            lambda: self.store.search_entities(
                profile_id=profile_id,
                names=analysis["entities"],
                limit=max(limit, 8),
            ),
            failed_branches,
        ))

        fused = fuse_candidates(candidates)
        seed_ids = [item["memory_id"] for item in fused[: max(limit, 4)]]
        try:
            relation_candidates, relations = expand_relation_candidates(
                store=self.store,
                profile_id=profile_id,
                seed_ids=seed_ids,
                existing_ids={item["memory_id"] for item in fused},
            )
        except Exception:
            failed_branches.append("relations")
            relation_candidates, relations = [], []
        fused = fuse_candidates([*fused, *relation_candidates])
        try:
            pre_rerank_memories = hydrate_and_rank_memories(
                store=self.store,
                profile_id=profile_id,
                fused_candidates=fused,
                limit=max(limit * 2, limit),
            )
        except Exception:
            failed_branches.append("hydration")
            pre_rerank_memories = []
        try:
            ranked_memories = self.reranker.rerank(
                query=query,
                memories=pre_rerank_memories,
                analysis=analysis,
                limit=limit,
            )
        except Exception:
            failed_branches.append("rerank")
            ranked_memories = pre_rerank_memories[:limit]
        returned_relations = filter_relations_for_ranked_memories(
            relations=relations,
            ranked_memories=ranked_memories,
        )

        trace = build_recall_trace(
            candidates=candidates,
            relation_candidates=relation_candidates,
            relations=returned_relations,
            pre_rerank_memories=pre_rerank_memories,
            ranked_memories=ranked_memories,
        )
        trace["failed_branches"] = failed_branches
        return {
            "query": query,
            "analysis": analysis,
            "memories": ranked_memories,
            "relations": returned_relations,
            "packed_context": pack_context(
                ranked_memories,
                max_chars=context_budget_chars(context_hints),
            ),
            "trace": trace,
        }


def context_budget_chars(context_hints: dict[str, Any]) -> int | None:
    value = context_hints.get("context_budget_chars")
    if value is None:
        return None

    return max(int(value), 0)


def _safe_seeds(
    branch: str,
    retrieve: Callable[[], Any],
    failed_branches: list[str],
) -> list[dict[str, Any]]:
    """Return only well-formed candidates and reduce branch failures to one safe label."""

    try:
        raw = retrieve()
        if not isinstance(raw, list):
            raise TypeError("seed result must be a list")
        result: list[dict[str, Any]] = []
        malformed = False
        for item in raw:
            if not isinstance(item, Mapping):
                malformed = True
                continue
            memory_id = item.get("memory_id")
            score = item.get("score")
            if (
                not isinstance(memory_id, str)
                or not memory_id
                or isinstance(score, bool)
                or not isinstance(score, int | float)
                or not math.isfinite(float(score))
            ):
                malformed = True
                continue
            result.append({**item, "memory_id": memory_id, "score": float(score)})
        if malformed:
            failed_branches.append(branch)
        return result
    except Exception:
        failed_branches.append(branch)
        return []
