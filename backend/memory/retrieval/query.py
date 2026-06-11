from __future__ import annotations

import re
from typing import Any


class QueryAnalyzer:
    def __init__(self, default_now_iso: str | None = None):
        self.default_now_iso = default_now_iso

    def analyze(
        self,
        *,
        query: str,
        context_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context_hints = context_hints or {}
        entities = dedupe(
            [
                *[
                    str(value).strip()
                    for value in context_hints.get("entities", [])
                    if str(value).strip()
                ],
                *extract_query_entities(query),
            ]
        )
        now_iso = context_hints.get("now_iso") or self.default_now_iso
        time = {}

        if now_iso and is_current_time_query(query):
            time["valid_at"] = str(now_iso)

        return {
            "entities": entities,
            "time": time,
        }


def extract_query_entities(query: str) -> list[str]:
    entities = []

    for keyword in ["热拿铁", "黑咖啡", "咖啡豆", "咖啡店", "项目", "周报", "阿杰"]:
        if keyword in query:
            entities.append(keyword)

    for match in re.finditer(r"(?:和|跟)([^，。,.！!？?\s]{2,8})", query):
        entities.append(cleanup_entity(match.group(1)))

    return dedupe(entities)


def is_current_time_query(query: str) -> bool:
    return bool(re.search(r"今天|现在|当前|目前|最近|到期|提醒", query))


def cleanup_entity(value: str) -> str:
    return value.strip("，。,.！!？? ")


def dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
