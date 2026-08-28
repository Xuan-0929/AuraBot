"""Turn raw content into validated Memory candidates for synchronous and async ingestion paths.

Legacy calls remain synchronous. Main Agent conversation ingestion prefers a model's cancellable async
boundary and otherwise offloads the synchronous extractor, while all schema parsing stays shared here.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from .extraction.providers import MemoryExtractionModel
from .extraction.schemas import parse_structured_extraction_payload
from .types import EntityCandidate, MemoryCandidate


class MemoryExtractor(Protocol):
    def extract(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> tuple[list[MemoryCandidate], list[EntityCandidate]]:
        ...


class StructuredMemoryExtractor:
    def __init__(self, model: MemoryExtractionModel):
        self.model = model

    def extract(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> tuple[list[MemoryCandidate], list[EntityCandidate]]:
        payload = self.model.extract_memory_payload(
            content=content,
            metadata=metadata,
            memory_type=memory_type,
            importance=importance,
        )
        return parse_structured_extraction_payload(
            payload,
            base_metadata=metadata,
            default_importance=importance,
        )

    async def extract_async(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> tuple[list[MemoryCandidate], list[EntityCandidate]]:
        """Extract through a cancellable model when available, preserving one validation path."""

        extract = getattr(self.model, "extract_memory_payload_async", None)
        if not callable(extract):
            return await asyncio.to_thread(
                self.extract,
                content=content,
                metadata=metadata,
                memory_type=memory_type,
                importance=importance,
            )
        payload = await extract(
            content=content,
            metadata=metadata,
            memory_type=memory_type,
            importance=importance,
        )
        return parse_structured_extraction_payload(
            payload,
            base_metadata=metadata,
            default_importance=importance,
        )


class RuleBasedMemoryExtractor:
    def extract(
        self,
        *,
        content: str,
        metadata: dict[str, Any],
        memory_type: str | None = None,
        importance: float = 0.5,
    ) -> tuple[list[MemoryCandidate], list[EntityCandidate]]:
        explicit_entities = [str(value) for value in metadata.get("entities", [])]
        valid_from = metadata.get("valid_from")
        valid_to = metadata.get("valid_to")

        if memory_type and memory_type != "semantic":
            entities = dedupe([*explicit_entities, *extract_entities(content)])
            candidate_metadata = enrich_preference_metadata(content, metadata)
            return (
                [
                    MemoryCandidate(
                        content=content,
                        memory_type=memory_type,
                        importance=importance,
                        confidence=0.82,
                        valid_from=valid_from,
                        valid_to=valid_to,
                        entities=entities,
                        metadata=candidate_metadata,
                    )
                ],
                [EntityCandidate(name=entity) for entity in entities],
            )

        candidates: list[MemoryCandidate] = []
        entities = dedupe([*explicit_entities, *extract_entities(content)])

        for preference in extract_positive_preferences(content):
            candidates.append(
                MemoryCandidate(
                    content=f"用户喜欢{preference}",
                    memory_type="preference",
                    importance=max(importance, 0.72),
                    confidence=0.78,
                    entities=dedupe([preference, *entities]),
                    metadata={**metadata, "preference_polarity": "positive"},
                )
            )

        for preference in extract_negative_preferences(content):
            candidates.append(
                MemoryCandidate(
                    content=f"用户不喜欢{preference}",
                    memory_type="preference",
                    importance=max(importance, 0.72),
                    confidence=0.78,
                    entities=dedupe([preference, *entities]),
                    metadata={**metadata, "preference_polarity": "negative"},
                )
            )

        if is_time_event(content):
            event_type = "follow_up" if has_reminder_intent(content) else "event"
            candidates.append(
                MemoryCandidate(
                    content=content if content.startswith("用户") else f"用户提到：{content}",
                    memory_type=event_type,
                    importance=max(importance, 0.76),
                    confidence=0.74,
                    valid_from=valid_from or infer_default_valid_from(content, metadata),
                    valid_to=valid_to,
                    entities=entities,
                    metadata=metadata,
                )
            )

        if not candidates:
            candidates.append(
                MemoryCandidate(
                    content=content,
                    memory_type="semantic",
                    importance=importance,
                    confidence=0.65,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    entities=entities,
                    metadata=metadata,
                )
            )

        return candidates, [EntityCandidate(name=entity) for entity in entities]


def extract_positive_preferences(content: str) -> list[str]:
    values = []

    for match in re.finditer(r"(?<!不)(?:我|用户)?喜欢(?:吃|喝|玩|看)?([^，。,.！!？?\s]+)", content):
        value = cleanup_entity(match.group(1))
        if value:
            values.append(value)

    return dedupe(values)


def extract_negative_preferences(content: str) -> list[str]:
    values = []

    for match in re.finditer(r"(?:我|用户)?(?:不喜欢|讨厌|不爱)(?:吃|喝|玩|看)?([^，。,.！!？?\s]+)", content):
        value = cleanup_entity(match.group(1))
        if value:
            values.append(value)

    return dedupe(values)


def extract_preferences(content: str) -> list[str]:
    return dedupe([*extract_positive_preferences(content), *extract_negative_preferences(content)])


def extract_entities(content: str) -> list[str]:
    entities = []

    for value in extract_preferences(content):
        entities.append(value)

    for match in re.finditer(r"(?:和|跟)([^，。,.！!？?\s]{2,8})", content):
        entities.append(cleanup_entity(match.group(1)))

    for match in re.finditer(r"(?:提交|讨论|整理|购买|买|去)([^，。,.！!？?\s]{2,12})", content):
        entities.append(cleanup_entity(match.group(1)))

    for keyword in ["热拿铁", "黑咖啡", "咖啡豆", "咖啡店", "项目", "周报", "阿杰"]:
        if keyword in content:
            entities.append(keyword)

    return dedupe([entity for entity in entities if entity])


def enrich_preference_metadata(content: str, metadata: dict[str, Any]) -> dict[str, Any]:
    if metadata.get("preference_polarity"):
        return metadata

    if extract_negative_preferences(content):
        return {**metadata, "preference_polarity": "negative"}

    if extract_positive_preferences(content):
        return {**metadata, "preference_polarity": "positive"}

    return metadata


def is_time_event(content: str) -> bool:
    return bool(re.search(r"明天|后天|今晚|下周|下个月|提醒|别忘|记得|提交|开会", content))


def has_reminder_intent(content: str) -> bool:
    return bool(re.search(r"提醒|别忘|记得", content))


def infer_default_valid_from(content: str, metadata: dict[str, Any]) -> str | None:
    if "valid_from" in metadata:
        return metadata["valid_from"]

    if has_reminder_intent(content):
        return "2099-01-01T00:00:00+00:00"

    return None


def cleanup_entity(value: str) -> str:
    return (
        value.replace("。", "")
        .replace("，", "")
        .replace("提醒我", "")
        .replace("也", "")
        .strip()
    )


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
