from __future__ import annotations

from typing import Any

from .consolidator import AddFirstConsolidator
from .extractor import MemoryExtractor, RuleBasedMemoryExtractor
from .retriever import HybridMemoryRetriever
from .stores.sqlite import SQLiteMemoryStore
from .types import EmbeddingProvider, MemoryCandidate, VectorStore
from .validators import validate_memory_candidate


class MemoryService:
    def __init__(
        self,
        *,
        metadata_store: SQLiteMemoryStore,
        vector_store: VectorStore,
        embedding_provider: EmbeddingProvider,
        extractor: MemoryExtractor | None = None,
    ):
        self.metadata_store = metadata_store
        self.vector_store = vector_store
        self.embedding_provider = embedding_provider
        self.extractor = extractor or RuleBasedMemoryExtractor()
        self.retriever = HybridMemoryRetriever(
            store=metadata_store,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
        )
        self.consolidator = AddFirstConsolidator(metadata_store)

    def close(self) -> None:
        self.metadata_store.close()
        close_vector_store = getattr(self.vector_store, "close", None)

        if close_vector_store:
            close_vector_store()

    def remember(
        self,
        *,
        profile_id: str,
        content: str,
        source_type: str,
        source_id: str | None = None,
        role: str | None = None,
        memory_type: str | None = None,
        importance: float = 0.5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        episode_id = self.metadata_store.create_episode(
            profile_id=profile_id,
            source_type=source_type,
            source_id=source_id,
            role=role,
            content=content,
            metadata=metadata,
        )
        try:
            candidates, entities = self.extractor.extract(
                content=content,
                metadata=metadata,
                memory_type=memory_type,
                importance=importance,
            )
            candidates = [validate_memory_candidate(candidate) for candidate in candidates]
        except Exception as exc:
            self.metadata_store.write_audit_log(
                profile_id=profile_id,
                action="remember",
                input_summary=content,
                result_json={"episode_id": episode_id, "memory_ids": []},
                error=str(exc),
            )
            raise

        entity_ids_by_name = {}

        for entity in entities:
            entity_ids_by_name[entity.name] = self.metadata_store.upsert_entity(
                profile_id=profile_id,
                candidate=entity,
            )

        memory_ids = []

        for candidate in candidates:
            memory_id = self.metadata_store.create_memory(
                profile_id=profile_id,
                episode_id=episode_id,
                candidate=candidate,
            )
            memory_ids.append(memory_id)
            self._index_memory(
                profile_id=profile_id,
                memory_id=memory_id,
                episode_id=episode_id,
                source_type=source_type,
                candidate=candidate,
                entity_ids=[
                    entity_ids_by_name[name]
                    for name in candidate.entities
                    if name in entity_ids_by_name
                ],
            )
            self._create_relations_for_new_memory(
                profile_id=profile_id,
                episode_id=episode_id,
                memory_id=memory_id,
                candidate=candidate,
            )

        result = {
            "episode_id": episode_id,
            "memory_ids": memory_ids,
            "memory_id": memory_ids[0] if memory_ids else None,
            "state": "active",
        }
        self.metadata_store.write_audit_log(
            profile_id=profile_id,
            action="remember",
            input_summary=content,
            result_json=result,
        )
        return result

    def recall(
        self,
        *,
        profile_id: str,
        query: str,
        limit: int = 8,
        filters: dict[str, Any] | None = None,
        context_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self.retriever.recall(
            profile_id=profile_id,
            query=query,
            limit=limit,
            filters=filters,
            context_hints=context_hints,
        )
        result["source_episodes"] = self.metadata_store.get_episodes(
            profile_id=profile_id,
            episode_ids=list(
                dict.fromkeys(
                    memory["episode_id"]
                    for memory in result["memories"]
                    if memory.get("episode_id")
                )
            ),
        )
        self.metadata_store.write_audit_log(
            profile_id=profile_id,
            action="recall",
            input_summary=query,
            result_json={
                "memory_ids": [memory["memory_id"] for memory in result["memories"]],
                "trace": result["trace"],
            },
        )
        return result

    def forget(
        self,
        *,
        profile_id: str,
        memory_ids: list[str] | None = None,
        episode_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        if not memory_ids and not episode_ids:
            raise ValueError("forget requires memory_ids or episode_ids")

        target_ids = list(memory_ids or [])
        target_ids.extend(
            self.metadata_store.memory_ids_for_episodes(
                profile_id=profile_id,
                episode_ids=episode_ids or [],
            )
        )
        target_ids = list(dict.fromkeys(target_ids))
        forgotten_count = self.metadata_store.archive_memories(
            profile_id=profile_id,
            memory_ids=target_ids,
            reason=reason,
        )
        self.vector_store.delete(target_ids)
        result = {
            "forgotten_count": forgotten_count,
            "memory_ids": target_ids,
        }
        self.metadata_store.write_audit_log(
            profile_id=profile_id,
            action="forget",
            input_summary=reason or "forget",
            result_json=result,
        )
        return result

    def improve(self, profile_id: str) -> dict[str, Any]:
        result = self.consolidator.improve(profile_id)
        inactive_ids = [
            memory["memory_id"]
            for memory in self.metadata_store.debug_snapshot(profile_id)["memories"]
            if memory["state"] in {"stale", "superseded"}
        ]
        self.vector_store.delete(inactive_ids)
        return result

    def due(self, profile_id: str, now_iso: str) -> dict[str, Any]:
        memories = self.metadata_store.get_due_memories(
            profile_id=profile_id,
            now_iso_value=now_iso,
        )
        result = {"memories": memories}
        self.metadata_store.write_audit_log(
            profile_id=profile_id,
            action="due",
            input_summary=now_iso,
            result_json={"memory_ids": [memory["memory_id"] for memory in memories]},
        )
        return result

    def debug(self, profile_id: str) -> dict[str, Any]:
        return self.metadata_store.debug_snapshot(profile_id)

    def health(self) -> dict[str, Any]:
        return {
            "sqlite": self.metadata_store.health(),
            "qdrant": self.vector_store.health(),
            "embedding": {
                "provider": self.embedding_provider.__class__.__name__,
                "dimension": self.embedding_provider.dimension,
            },
        }

    def _index_memory(
        self,
        *,
        profile_id: str,
        memory_id: str,
        episode_id: str,
        source_type: str,
        candidate: MemoryCandidate,
        entity_ids: list[str],
    ) -> None:
        vector_text = " ".join([candidate.content, *candidate.entities])
        self.vector_store.upsert(
            memory_id,
            self.embedding_provider.embed(vector_text),
            {
                "memory_id": memory_id,
                "episode_id": episode_id,
                "profile_id": profile_id,
                "memory_type": candidate.memory_type,
                "state": "active",
                "entity_ids": entity_ids,
                "valid_from": candidate.valid_from,
                "valid_to": candidate.valid_to,
                "importance": candidate.importance,
                "confidence": candidate.confidence,
                "source_type": source_type,
                **scalar_payload(candidate.metadata),
            },
        )

    def _create_relations_for_new_memory(
        self,
        *,
        profile_id: str,
        episode_id: str,
        memory_id: str,
        candidate: MemoryCandidate,
    ) -> None:
        existing = [
            memory
            for memory in self.metadata_store.list_active_memories(profile_id)
            if memory["memory_id"] != memory_id
        ]
        candidate_entities = set(candidate.entities)

        for memory in existing:
            memory_entities = set(memory["metadata"].get("entities", []))

            if candidate_entities & memory_entities or same_broad_topic(candidate.content, memory["content"]):
                self.metadata_store.create_relation(
                    profile_id=profile_id,
                    source_id=memory_id,
                    target_id=memory["memory_id"],
                    relation_type=choose_relation_type(candidate.memory_type, memory["memory_type"]),
                    weight=0.78,
                    confidence=0.72,
                    episode_id=episode_id,
                    valid_from=candidate.valid_from,
                    valid_to=candidate.valid_to,
                )


def choose_relation_type(source_type: str, target_type: str) -> str:
    if source_type == "follow_up" and target_type in {"event", "preference"}:
        return "scheduled_for"

    if source_type == "event" and target_type == "preference":
        return "preference_for"

    return "relates_to"


def same_broad_topic(left: str, right: str) -> bool:
    coffee_terms = ["咖啡", "拿铁", "冷萃", "咖啡豆", "咖啡店"]
    project_terms = ["项目", "周报"]

    return (
        any(term in left for term in coffee_terms) and any(term in right for term in coffee_terms)
    ) or (
        any(term in left for term in project_terms) and any(term in right for term in project_terms)
    )


def scalar_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if isinstance(value, str | int | float | bool)
    }
