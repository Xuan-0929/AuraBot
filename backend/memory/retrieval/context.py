from __future__ import annotations

from typing import Any


def pack_context(memories: list[dict[str, Any]], *, max_chars: int | None = None) -> dict[str, Any]:
    budget = max_chars if max_chars is not None else 2400
    parts: list[str] = []
    memory_ids: list[str] = []
    char_count = 0

    for memory in memories:
        content = str(memory.get("content", "")).strip()
        memory_id = memory.get("memory_id")

        if not content or not memory_id:
            continue

        next_count = char_count + len(content)
        if parts:
            next_count += 1

        if parts and next_count > budget:
            break

        if not parts and len(content) > budget:
            content = content[:budget]
            next_count = len(content)

        parts.append(content)
        memory_ids.append(memory_id)
        char_count = next_count

    return {
        "text": "\n".join(parts),
        "memory_ids": memory_ids,
        "char_count": char_count,
        "max_chars": budget,
    }
