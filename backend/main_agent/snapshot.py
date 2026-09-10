"""Project canonical profile facts into the bounded JSON payload consumed by control invokes.

ContextStore supplies ordered heard conversation; Runtime identifies every segment in the current ActiveInput
and supplies Memory plus the frozen tool surface. The projector preserves current input ahead of older variable
facts, deterministically truncates it only when required, and always keeps runtime/tool authority. Soul is a
separate complete system message with its own bound; an insufficient budget raises instead of truncating it. User-provided workspace names and absolute paths are static location context,
not Pi filesystem permissions; projection does not scan those directories.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .context import FoldedContext


@dataclass(frozen=True, slots=True)
class SnapshotBudget:
    """Total and section character bounds for one provider request."""

    max_prompt_chars: int = 12_000
    soul_chars: int = 34 * 1024
    conversation_chars: int = 6_000
    summary_chars: int = 2_000
    observation_chars: int = 3_000
    memory_chars: int = 2_400

    def __post_init__(self) -> None:
        if min(
            self.max_prompt_chars,
            self.soul_chars,
            self.conversation_chars,
            self.summary_chars,
            self.observation_chars,
            self.memory_chars,
        ) < 0 or self.max_prompt_chars < 2:
            raise ValueError("snapshot budgets must be non-negative and fit a JSON object")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Independent JSON-native facts frozen for exactly one invoke."""

    conversation_turns: tuple[dict[str, Any], ...]
    conversation_summaries: tuple[dict[str, Any], ...]
    soul: str
    observations: tuple[dict[str, Any], ...]
    think_note: str | None
    task_facts: tuple[dict[str, Any], ...]
    memory_facts: tuple[dict[str, Any], ...]
    runtime_state: dict[str, Any]
    allowed_tool_surface: tuple[dict[str, Any], ...]
    prompt: str


class SnapshotProjector:
    """Create a compact provider payload without compatibility copies or mutable source aliases."""

    def __init__(self, budget: SnapshotBudget | None = None, *, workspaces: Mapping[str, str] | None = None) -> None:
        self.budget = budget or SnapshotBudget()
        self.workspaces = dict(workspaces or {})

    def project(
        self,
        context: FoldedContext,
        *,
        soul: str,
        memory_facts: Sequence[Mapping[str, Any]] = (),
        runtime_state: Mapping[str, Any] | None = None,
        allowed_tools: Sequence[Mapping[str, Any]] = (),
        protected_segment_ids: Sequence[str] = (),
    ) -> Snapshot:
        """Copy JSON values, apply section limits, then evict oldest facts until within total budget."""

        if not isinstance(soul, str):
            raise TypeError("soul must be text")
        if len(soul) > self.budget.soul_chars:
            raise ValueError("soul_budget_exceeded")
        protected = frozenset(str(value) for value in protected_segment_ids)
        conversation = _conversation_records(
            context.conversation_turns,
            self.budget.conversation_chars,
            protected,
        )
        summaries = _recent(context.conversation_summaries, self.budget.summary_chars)
        observations = _observations(context.observations, self.budget.observation_chars)
        memory = _records(memory_facts, self.budget.memory_chars)
        task_facts = _copy(context.task_facts)
        runtime = _object(runtime_state or {})
        tools = _copy(allowed_tools)
        sections: dict[str, Any] = {
            "conversation": conversation,
            "conversation_summaries": summaries,
            "observations": observations,
            "think_note": context.think_note,
            "task_facts": task_facts,
            "memory_facts": memory,
            "runtime_state": runtime,
            "allowed_tool_surface": tools,
        }
        if self.workspaces:
            sections["user_workspaces"] = {
                "purpose": "User-provided directory references for understanding projects and choosing Task starting locations; not Pi filesystem access boundaries.",
                "directories": [{"id": key, "path": path} for key, path in self.workspaces.items()],
            }
        _fit(sections, self.budget.max_prompt_chars, protected)
        prompt = _encode(sections)
        return Snapshot(
            conversation_turns=tuple(_copy(sections["conversation"])),
            conversation_summaries=tuple(_copy(sections["conversation_summaries"])),
            soul=soul,
            observations=tuple(_copy(sections["observations"])),
            think_note=sections["think_note"],
            task_facts=tuple(_copy(sections["task_facts"])),
            memory_facts=tuple(_copy(sections["memory_facts"])),
            runtime_state=_object(sections["runtime_state"]),
            allowed_tool_surface=tuple(_copy(sections["allowed_tool_surface"])),
            prompt=prompt,
        )


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _copy(value: Any) -> Any:
    try:
        return json.loads(_encode(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("snapshot accepts only finite JSON values with string keys") from exc


def _object(value: Mapping[str, Any]) -> dict[str, Any]:
    copied = _copy(value)
    if not isinstance(copied, dict):
        raise TypeError("snapshot mapping must be an object")
    return copied


def _records(values: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 0
    for value in values:
        copied = _object(value)
        size = len(_encode(copied))
        if used + size > limit:
            continue
        result.append(copied)
        used += size
    return result


def _observations(values: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Pack newest facts first; compact only the newest oversized fact and skip oversized older facts."""

    result: list[dict[str, Any]] = []
    used = 0
    for index, value in enumerate(reversed(values)):
        copied = _object(value)
        size = len(_encode(copied))
        if used + size <= limit:
            result.append(copied)
            used += size
            continue
        if index == 0:
            compact = _compact_observation(copied, limit - used)
            if compact is not None:
                result.append(compact)
                used += len(_encode(compact))
    result.reverse()
    return result


def _compact_observation(value: Mapping[str, Any], limit: int) -> dict[str, Any] | None:
    kind = str(value.get("kind", ""))[:64]
    compact: dict[str, Any] = {
        "id": str(value.get("id", ""))[:256],
        "kind": kind,
        "status": str(value.get("status", ""))[:32],
        "request": _compact_request(kind, value.get("request")),
        "truncated": True,
    }
    if compact["status"] == "failed":
        compact["error_type"] = str(value.get("error_type", "ExecutorError"))[:128]
        return compact if len(_encode(compact)) <= limit else None

    raw_result = value.get("result")
    if kind == "web_search" and isinstance(raw_result, Mapping):
        results = raw_result.get("results")
        compact["result"] = {
            "query": str(raw_result.get("query", ""))[:400],
            "results": [
                {
                    "source_id": str(item.get("source_id", ""))[:128],
                    "title": str(item.get("title", ""))[:160],
                    "snippet": str(item.get("snippet", ""))[:400],
                }
                for item in (results if isinstance(results, list) else ())[:3]
                if isinstance(item, Mapping)
            ],
        }
        while compact["result"]["results"] and len(_encode(compact)) > limit:
            compact["result"]["results"].pop()
    elif kind == "workspace_list" and isinstance(raw_result, Mapping):
        items = raw_result.get("items")
        compact["result"] = {
            "workspace_id": str(raw_result.get("workspace_id", ""))[:64],
            "path": str(raw_result.get("path", ""))[:512],
            "items": [
                {
                    "name": str(item.get("name", ""))[:120],
                    "type": str(item.get("type", ""))[:32],
                    "size": item.get("size", 0),
                }
                for item in (items if isinstance(items, list) else ())[:10]
                if isinstance(item, Mapping)
            ],
            "truncated": bool(raw_result.get("truncated", False)),
        }
        while compact["result"]["items"] and len(_encode(compact)) > limit:
            compact["result"]["items"].pop()
    elif kind == "workspace_read" and isinstance(raw_result, Mapping):
        projected = {
            "workspace_id": str(raw_result.get("workspace_id", ""))[:64],
            "path": str(raw_result.get("path", ""))[:512],
            "start_line": raw_result.get("start_line"),
            "next_start_line": raw_result.get("next_start_line"),
            "content": "",
        }
        compact["result"] = projected
        _fit_text(compact, projected, "content", str(raw_result.get("content", "")), limit)
    elif kind == "web_read" and isinstance(raw_result, Mapping):
        projected = {
            "source_id": str(raw_result.get("source_id", ""))[:128],
            "content": "",
        }
        compact["result"] = projected
        _fit_text(compact, projected, "content", str(raw_result.get("content", "")), limit)
    else:
        projected = {"preview": ""}
        compact["result"] = projected
        _fit_text(compact, projected, "preview", _encode(raw_result), limit)
    return compact if len(_encode(compact)) <= limit else None


def _compact_request(kind: str, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    fields = {
        "web_search": ("query",),
        "web_read": ("source_id",),
        "workspace_list": ("workspace_id", "path"),
        "workspace_read": ("workspace_id", "path", "start_line", "max_lines"),
    }.get(kind, tuple(sorted(str(key) for key in raw)[:8]))
    result: dict[str, Any] = {}
    for key in fields:
        item = raw.get(key)
        result[key] = item[:512] if isinstance(item, str) else item
    return _object(result)


def _fit_text(record: dict[str, Any], owner: dict[str, Any], key: str, text: str, limit: int) -> None:
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        owner[key] = text[:middle]
        if len(_encode(record)) <= limit:
            low = middle
        else:
            high = middle - 1
    owner[key] = text[:low]


def _recent(values: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    used = 0
    for value in reversed(values):
        copied = _object(value)
        size = len(_encode(copied))
        if used + size > limit:
            break
        result.append(copied)
        used += size
    result.reverse()
    return result


def _conversation_records(
    values: Sequence[Mapping[str, Any]],
    limit: int,
    protected_segment_ids: frozenset[str],
) -> list[dict[str, Any]]:
    """Fit recent history around all current-input segments, truncating protected text last."""

    copied = [_object(value) for value in values]
    protected_indexes = {
        index for index, value in enumerate(copied)
        if value.get("segment_id") in protected_segment_ids
    }
    protected = [copied[index] for index in sorted(protected_indexes)]
    if sum(len(_encode(value)) for value in protected) > limit:
        protected = _truncate_protected_records(protected, limit)
    used = sum(len(_encode(value)) for value in protected)
    selected = {index for index in protected_indexes}
    for index in range(len(copied) - 1, -1, -1):
        if index in protected_indexes:
            continue
        size = len(_encode(copied[index]))
        if used + size <= limit:
            selected.add(index)
            used += size
    protected_by_id = {
        value.get("segment_id"): value for value in protected
    }
    return [
        protected_by_id.get(copied[index].get("segment_id"), copied[index])
        for index in sorted(selected)
        if index not in protected_indexes or copied[index].get("segment_id") in protected_by_id
    ]


def _truncate_protected_records(
    values: list[dict[str, Any]], limit: int,
) -> list[dict[str, Any]]:
    """Keep at least the newest non-empty segment and grow text newest-first within a section bound."""

    originals = [str(value.get("content", "")) for value in values]
    result = [dict(value, content=text[:1]) for value, text in zip(values, originals, strict=True)]
    if not result:
        return []
    if sum(len(_encode(value)) for value in result) > limit:
        return result
    for index in range(len(result) - 1, -1, -1):
        text = originals[index]
        low, high = len(result[index]["content"]), len(text)
        while low < high:
            middle = (low + high + 1) // 2
            candidate = dict(result[index], content=text[:middle])
            total = sum(
                len(_encode(candidate if offset == index else value))
                for offset, value in enumerate(result)
            )
            if total <= limit:
                low = middle
            else:
                high = middle - 1
        result[index]["content"] = text[:low]
    return result


def _fit(
    sections: dict[str, Any],
    limit: int,
    protected_segment_ids: frozenset[str],
) -> None:
    """Evict old variable facts before deterministically trimming the current ActiveInput."""

    variable_lists = (
        "conversation_summaries", "memory_facts", "observations", "task_facts",
    )
    while len(_encode(sections)) > limit:
        changed = False
        for key in variable_lists:
            values = sections[key]
            if values:
                values.pop(0)
                changed = True
                break
        if not changed:
            conversation = sections["conversation"]
            for index, value in enumerate(conversation):
                if value.get("segment_id") not in protected_segment_ids:
                    conversation.pop(index)
                    changed = True
                    break
        if not changed and sections["think_note"]:
            sections["think_note"] = None
            changed = True
        if not changed and sections["conversation"]:
            current = [
                value for value in sections["conversation"]
                if value.get("segment_id") in protected_segment_ids
            ]
            sections["conversation"] = _truncate_protected_for_payload(sections, current, limit)
            changed = len(_encode(sections)) <= limit
        if not changed:
            raise ValueError("max_prompt_chars cannot fit required runtime and tool surface")


def _truncate_protected_for_payload(
    sections: dict[str, Any],
    values: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    """Fit protected records against the exact final JSON envelope, retaining newest content first."""

    originals = [str(value.get("content", "")) for value in values]
    result = [dict(value, content=text[:1]) for value, text in zip(values, originals, strict=True)]
    sections["conversation"] = result
    if not result or len(_encode(sections)) > limit:
        return result
    for index in range(len(result) - 1, -1, -1):
        text = originals[index]
        low, high = len(result[index]["content"]), len(text)
        while low < high:
            middle = (low + high + 1) // 2
            result[index]["content"] = text[:middle]
            if len(_encode(sections)) <= limit:
                low = middle
            else:
                high = middle - 1
        result[index]["content"] = text[:low]
    return result


__all__ = ["Snapshot", "SnapshotBudget", "SnapshotProjector"]
