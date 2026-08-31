"""Pack ranked Memory content into the exact bounded JSON record consumed by Main Agent.

The retriever owns content selection and stable memory identifiers.  This module counts the compact
``{"text": ..., "memory_ids": [...]}`` envelope itself, so Snapshot projection receives a record that fits
without learning Memory's internal rows or relying on a matching text-only constant.
"""

from __future__ import annotations

import json
from typing import Any


def pack_context(memories: list[dict[str, Any]], *, max_chars: int | None = None) -> dict[str, Any]:
    budget = max(int(max_chars), 0) if max_chars is not None else 2400
    parts: list[str] = []
    memory_ids: list[str] = []
    if budget < _record_size(parts, memory_ids):
        raise ValueError("max_chars cannot fit the fixed JSON envelope")

    for memory in memories:
        content = str(memory.get("content", "")).strip()
        memory_id = str(memory.get("memory_id", "")).strip()

        if not content or not memory_id:
            continue

        proposed_parts = [*parts, content]
        proposed_ids = [*memory_ids, memory_id]
        if _record_size(proposed_parts, proposed_ids) <= budget:
            parts, memory_ids = proposed_parts, proposed_ids
            continue

        low, high = 0, len(content)
        while low < high:
            middle = (low + high + 1) // 2
            if _record_size([*parts, content[:middle]], proposed_ids) <= budget:
                low = middle
            else:
                high = middle - 1
        if low:
            parts.append(content[:low])
            memory_ids.append(memory_id)
            break

    char_count = _record_size(parts, memory_ids)

    return {
        "text": "\n".join(parts),
        "memory_ids": memory_ids,
        "char_count": char_count,
        "max_chars": budget,
    }


def _record_size(parts: list[str], memory_ids: list[str]) -> int:
    return len(json.dumps(
        {"text": "\n".join(parts), "memory_ids": memory_ids},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))
