from __future__ import annotations

from typing import Any

from .retrieval.context import pack_context
from .retrieval.fusion import fuse_candidates
from .retrieval.query import QueryAnalyzer
from .retrieval.ranking import filter_relations_for_ranked_memories, hydrate_and_rank_memories
from .retrieval.rerank import MemoryReranker, RuleBasedMemoryReranker
from .retrieval.relations import expand_relation_candidates
from .retrieval.seeds import EntitySeedRetriever, FtsSeedRetriever, VectorSeedRetriever
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
        self.vector_seeds = VectorSeedRetriever(
            vector_store=vector_store,
            embedding_provider=embedding_provider,
        )
        self.fts_seeds = FtsSeedRetriever(store)
        self.entity_seeds = EntitySeedRetriever(store)
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

        candidates: list[dict[str, Any]] = []
        candidates.extend(
            self.vector_seeds.retrieve(
                profile_id=profile_id,
                query=query,
                limit=limit,
                filters=retrieval_filters,
            )
        )
        candidates.extend(
            self.fts_seeds.retrieve(
                profile_id=profile_id,
                query=query,
                limit=limit,
            )
        )
        candidates.extend(
            self.entity_seeds.retrieve(
                profile_id=profile_id,
                entity_names=analysis["entities"],
                limit=limit,
            )
        )

        fused = fuse_candidates(candidates)
        seed_ids = [item["memory_id"] for item in fused[: max(limit, 4)]]
        relation_candidates, relations = expand_relation_candidates(
            store=self.store,
            profile_id=profile_id,
            seed_ids=seed_ids,
            existing_ids={item["memory_id"] for item in fused},
        )
        fused = fuse_candidates([*fused, *relation_candidates])
        pre_rerank_memories = hydrate_and_rank_memories(
            store=self.store,
            profile_id=profile_id,
            fused_candidates=fused,
            limit=max(limit * 2, limit),
        )
        ranked_memories = self.reranker.rerank(
            query=query,
            memories=pre_rerank_memories,
            analysis=analysis,
            limit=limit,
        )
        returned_relations = filter_relations_for_ranked_memories(
            relations=relations,
            ranked_memories=ranked_memories,
        )

        return {
            "query": query,
            "analysis": analysis,
            "memories": ranked_memories,
            "relations": returned_relations,
            "packed_context": pack_context(
                ranked_memories,
                max_chars=context_budget_chars(context_hints),
            ),
            "trace": build_recall_trace(
                candidates=candidates,
                relation_candidates=relation_candidates,
                relations=returned_relations,
                pre_rerank_memories=pre_rerank_memories,
                ranked_memories=ranked_memories,
            ),
        }


def context_budget_chars(context_hints: dict[str, Any]) -> int | None:
    value = context_hints.get("context_budget_chars")
    if value is None:
        return None

    return max(int(value), 0)
