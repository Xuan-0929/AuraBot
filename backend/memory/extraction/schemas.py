from __future__ import annotations

from typing import Any

from ..types import EntityCandidate, MemoryCandidate


def parse_structured_extraction_payload(
    payload: dict[str, Any],
    *,
    base_metadata: dict[str, Any],
    default_importance: float,
) -> tuple[list[MemoryCandidate], list[EntityCandidate]]:
    if not isinstance(payload, dict):
        raise ValueError("structured memory extraction payload must be an object")

    raw_memories = payload.get("memories", [])
    raw_entities = payload.get("entities", [])

    if not isinstance(raw_memories, list):
        raise ValueError("structured memory extraction payload memories must be a list")

    if not isinstance(raw_entities, list):
        raise ValueError("structured memory extraction payload entities must be a list")

    candidates = [
        parse_memory_candidate(
            raw_memory,
            base_metadata=base_metadata,
            default_importance=default_importance,
        )
        for raw_memory in raw_memories
    ]
    entities = [parse_entity_candidate(raw_entity) for raw_entity in raw_entities]
    entity_names = {entity.name for entity in entities}

    for candidate in candidates:
        for entity in candidate.entities:
            if entity and entity not in entity_names:
                entities.append(EntityCandidate(name=entity))
                entity_names.add(entity)

    return candidates, entities


def parse_memory_candidate(
    raw_memory: Any,
    *,
    base_metadata: dict[str, Any],
    default_importance: float,
) -> MemoryCandidate:
    if not isinstance(raw_memory, dict):
        raise ValueError("structured memory candidate must be an object")

    content = str(raw_memory.get("content", "")).strip()
    memory_type = str(raw_memory.get("memory_type", "semantic")).strip()
    importance = float(raw_memory.get("importance", default_importance))
    confidence = float(raw_memory.get("confidence", 0.7))
    entities = [
        str(entity).strip()
        for entity in raw_memory.get("entities", [])
        if str(entity).strip()
    ]
    metadata = raw_memory.get("metadata", {})

    if not isinstance(metadata, dict):
        raise ValueError("structured memory candidate metadata must be an object")

    return MemoryCandidate(
        content=content,
        memory_type=memory_type,
        importance=importance,
        confidence=confidence,
        valid_from=raw_memory.get("valid_from"),
        valid_to=raw_memory.get("valid_to"),
        entities=dedupe(entities),
        metadata={
            **base_metadata,
            **metadata,
            "extraction_source": "structured",
        },
    )


def parse_entity_candidate(raw_entity: Any) -> EntityCandidate:
    if not isinstance(raw_entity, dict):
        raise ValueError("structured entity candidate must be an object")

    name = str(raw_entity.get("name", "")).strip()
    if not name:
        raise ValueError("structured entity candidate name cannot be empty")

    aliases = [
        str(alias).strip()
        for alias in raw_entity.get("aliases", [])
        if str(alias).strip()
    ]

    return EntityCandidate(
        name=name,
        entity_type=str(raw_entity.get("entity_type", "concept")).strip() or "concept",
        aliases=dedupe(aliases),
        summary=str(raw_entity.get("summary", "")).strip(),
    )


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
