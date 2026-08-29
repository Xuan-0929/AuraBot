"""Own long-term Memory ingestion, retrieval, forgetting, and conversation extraction.

Main Agent submits complete Profile conversation batches through the cancellable async entrypoint; this
service alone decides which candidates become durable SQLite truth and Qdrant index entries. Existing
synchronous APIs remain available to diagnostics and legacy callers without a second Memory owner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

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

        result = self._persist_candidates(
            profile_id=profile_id,
            episode_id=episode_id,
            source_type=source_type,
            candidates=candidates,
            entities=entities,
        )
        self.metadata_store.write_audit_log(
            profile_id=profile_id,
            action="remember",
            input_summary=content,
            result_json=result,
        )
        return result

    async def remember_conversation(
        self,
        *,
        profile_id: str,
        turns: Sequence[Mapping[str, str]],
        source_id: str,
    ) -> dict[str, Any]:
        """Extract and persist one stable Main Agent conversation batch exactly once."""

        identifier = str(profile_id).strip()
        stable_source = str(source_id).strip()
        if not identifier or not stable_source:
            raise ValueError("profile_id and source_id must be non-empty")
        content, turn_count = _conversation_content(turns)
        source_type = "main_agent_conversation"
        existing = self.metadata_store.get_episode_by_source(
            profile_id=identifier,
            source_type=source_type,
            source_id=stable_source,
        )
        if existing is not None and existing.get("summary"):
            memory_ids = self.metadata_store.memory_ids_for_episodes(
                profile_id=identifier,
                episode_ids=[existing["episode_id"]],
            )
            self._sync_episode_vectors(
                profile_id=identifier,
                episode_id=existing["episode_id"],
                source_type=source_type,
                memory_ids=memory_ids,
            )
            return _result(existing["episode_id"], memory_ids, existing["summary"])

        metadata = {"turn_count": turn_count}
        extract_conversation = getattr(self.extractor, "extract_conversation_async", None)
        if callable(extract_conversation):
            extraction = await extract_conversation(content=content, metadata=metadata)
            candidates = list(extraction.memories)
            entities = list(extraction.entities)
            extracted_summary = extraction.summary
        else:
            extract_async = getattr(self.extractor, "extract_async", None)
            if callable(extract_async):
                candidates, entities = await extract_async(content=content, metadata=metadata)
            else:
                candidates, entities = await asyncio.to_thread(
                    self.extractor.extract, content=content, metadata=metadata
                )
            extracted_summary = ""
        candidates = [validate_memory_candidate(candidate) for candidate in candidates]
        episode_id = existing["episode_id"] if existing is not None else f"episode_{uuid4().hex}"
        memories = [(f"memory_{uuid4().hex}", candidate) for candidate in candidates]
        summary = extracted_summary.strip()[:1_000] or _fallback_conversation_summary(turns)
        stored = self.metadata_store.persist_conversation(
            profile_id=identifier,
            source_type=source_type,
            source_id=stable_source,
            episode_id=episode_id,
            content=content,
            summary=summary,
            metadata=metadata,
            memories=memories,
            entities=entities,
            relations=self._conversation_relations(
                profile_id=identifier,
                episode_id=episode_id,
                memories=memories,
            ),
        )
        self._sync_episode_vectors(
            profile_id=identifier,
            episode_id=stored["episode_id"],
            source_type=source_type,
            memory_ids=stored["memory_ids"],
        )
        result = _result(stored["episode_id"], stored["memory_ids"], stored["summary"])
        try:
            self.metadata_store.write_audit_log(
                profile_id=identifier,
                action="remember_conversation",
                input_summary=content,
                result_json=result,
            )
        except Exception:
            pass
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
        try:
            self.metadata_store.write_audit_log(
                profile_id=profile_id,
                action="recall",
                input_summary=query,
                result_json={
                    "memory_ids": [memory["memory_id"] for memory in result["memories"]],
                    "trace": result["trace"],
                },
            )
        except Exception:
            pass
        return result

    async def recall_context_async(
        self,
        *,
        profile_id: str,
        query: str,
        limit: int = 6,
        max_chars: int = 2_400,
    ) -> dict[str, Any]:
        """Offload synchronous recall and expose only Memory-owned bounded prompt context."""

        budget = max(int(max_chars), 0)
        result = await asyncio.to_thread(
            self.recall,
            profile_id=profile_id,
            query=query,
            limit=limit,
            context_hints={"context_budget_chars": budget},
        )
        packed = result.get("packed_context", {})
        text = str(packed.get("text", ""))[:budget]
        memory_ids = [str(value) for value in packed.get("memory_ids", ()) if str(value)]
        return {
            "text": text,
            "memory_ids": memory_ids,
            "char_count": len(text),
            "max_chars": budget,
        }

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

    def _persist_candidates(
        self,
        *,
        profile_id: str,
        episode_id: str,
        source_type: str,
        candidates: Sequence[MemoryCandidate],
        entities: Sequence[Any],
    ) -> dict[str, Any]:
        entity_ids_by_name = {
            entity.name: self.metadata_store.upsert_entity(
                profile_id=profile_id,
                candidate=entity,
            )
            for entity in entities
        }
        memory_ids: list[str] = []
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
        return _result(episode_id, memory_ids)

    def _conversation_relations(
        self,
        *,
        profile_id: str,
        episode_id: str,
        memories: Sequence[tuple[str, MemoryCandidate]],
    ) -> list[dict[str, Any]]:
        previous = [
            memory
            for memory in self.metadata_store.list_active_memories(profile_id)
            if memory["episode_id"] != episode_id
        ]
        relations: list[dict[str, Any]] = []
        for memory_id, candidate in memories:
            candidate_entities = set(candidate.entities)
            for memory in previous:
                if not (
                    candidate_entities & set(memory["metadata"].get("entities", ()))
                    or same_broad_topic(candidate.content, memory["content"])
                ):
                    continue
                relations.append({
                    "relation_id": f"relation_{uuid4().hex}",
                    "source_id": memory_id,
                    "target_id": memory["memory_id"],
                    "relation_type": choose_relation_type(
                        candidate.memory_type, memory["memory_type"]
                    ),
                    "weight": 0.78,
                    "confidence": 0.72,
                    "episode_id": episode_id,
                    "valid_from": candidate.valid_from,
                    "valid_to": candidate.valid_to,
                })
            previous.append({
                "memory_id": memory_id,
                "memory_type": candidate.memory_type,
                "content": candidate.content,
                "metadata": {**candidate.metadata, "entities": candidate.entities},
            })
        return relations

    def _sync_episode_vectors(
        self,
        *,
        profile_id: str,
        episode_id: str,
        source_type: str,
        memory_ids: Sequence[str],
    ) -> None:
        memories = self.metadata_store.get_active_memories(list(memory_ids), profile_id)
        entity_names = [
            str(name)
            for memory in memories
            for name in memory["metadata"].get("entities", ())
            if str(name)
        ]
        entity_ids = self.metadata_store.entity_ids_by_names(
            profile_id=profile_id,
            names=entity_names,
        )
        for memory in memories:
            metadata = dict(memory["metadata"])
            entities = [str(name) for name in metadata.pop("entities", ()) if str(name)]
            candidate = MemoryCandidate(
                content=memory["content"],
                memory_type=memory["memory_type"],
                importance=memory["importance"],
                confidence=memory["confidence"],
                valid_from=memory["valid_from"],
                valid_to=memory["valid_to"],
                entities=entities,
                metadata=metadata,
            )
            self._index_memory(
                profile_id=profile_id,
                memory_id=memory["memory_id"],
                episode_id=episode_id,
                source_type=source_type,
                candidate=candidate,
                entity_ids=[entity_ids[name] for name in entities if name in entity_ids],
            )

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


def _conversation_content(turns: Sequence[Mapping[str, str]]) -> tuple[str, int]:
    if isinstance(turns, (str, bytes)):
        raise ValueError("turns must be a non-empty sequence")
    normalized: list[str] = []
    for turn in turns:
        if not isinstance(turn, Mapping):
            raise ValueError("each conversation turn must be an object")
        role = turn.get("role")
        content = turn.get("content")
        if role not in {"user", "assistant"}:
            raise ValueError("conversation turn role must be user or assistant")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("conversation turn content must be non-empty text")
        normalized.append(f"{role}: {content.strip()}")
    if not normalized:
        raise ValueError("turns must be a non-empty sequence")
    return "\n".join(normalized), len(normalized)


def _fallback_conversation_summary(turns: Sequence[Mapping[str, str]]) -> str:
    parts = [f"{turn['role']}: {turn['content'].strip()}" for turn in turns]
    summary = " | ".join(parts)
    return summary[-1_000:] or "对话已归档"


def _result(
    episode_id: str,
    memory_ids: Sequence[str],
    summary: str | None = None,
) -> dict[str, Any]:
    identifiers = list(memory_ids)
    result = {
        "episode_id": episode_id,
        "memory_ids": identifiers,
        "memory_id": identifiers[0] if identifiers else None,
        "state": "active",
    }
    if summary is not None:
        result["summary"] = summary
    return result
