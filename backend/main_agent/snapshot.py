"""Project ordered Canonical Context into a deterministic, immutable and bounded LLM fact snapshot.

This module deliberately does no scheduling or tool execution: ContextStore supplies only delivered/folded
facts, ProfileStore supplies Soul, and MainAgentRuntime supplies read-only facts plus the exact frozen
ToolSurface. The projector canonicalizes map ordering, clips descriptive sections, and preserves required
runtime/tool control structure inside the total budget so invokes stay bounded and capabilities never drift.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .context import FoldedContext


@dataclass(frozen=True, slots=True)
class SnapshotBudget:
    """Character budgets for prompt sections and their final deterministic JSON envelope."""

    max_prompt_chars: int = 12_000
    soul_chars: int = 2_000
    transcript_chars: int = 2_000
    delivered_chars: int = 2_000
    conversation_chars: int = 4_000
    observation_chars: int = 3_000
    memory_chars: int = 2_400
    runtime_chars: int = 800
    tool_chars: int = 1_000

    def __post_init__(self) -> None:
        if any(
            value < 0
            for value in (
                self.max_prompt_chars, self.soul_chars, self.transcript_chars,
                self.delivered_chars, self.conversation_chars, self.observation_chars, self.memory_chars,
                self.runtime_chars, self.tool_chars,
            )
        ):
            raise ValueError("snapshot budgets must be non-negative")
        if self.max_prompt_chars < 2:
            raise ValueError("max_prompt_chars must fit an empty JSON object")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Frozen prompt facts handed to exactly one control invoke; nested mappings are read-only."""

    transcript: str
    delivered_clauses: tuple[str, ...]
    conversation_turns: tuple[Mapping[str, Any], ...]
    soul: str
    observations: tuple[Mapping[str, Any], ...]
    think_note: str | None
    task_facts: tuple[Mapping[str, Any], ...]
    memory_facts: tuple[Mapping[str, Any], ...]
    runtime_state: Mapping[str, Any]
    allowed_tool_surface: tuple[Mapping[str, Any], ...]
    prompt: str


class SnapshotProjector:
    """Create compact immutable snapshots using explicit inputs rather than hidden mutable globals."""

    def __init__(self, budget: SnapshotBudget | None = None) -> None:
        self.budget = budget or SnapshotBudget()

    def project(
        self,
        context: FoldedContext,
        *,
        soul: str,
        memory_facts: Sequence[Mapping[str, Any]] = (),
        runtime_state: Mapping[str, Any] | None = None,
        allowed_tools: Sequence[Mapping[str, Any]] = (),
    ) -> Snapshot:
        """Return a stable, bounded projection containing no unheard speech drafts or raw logs."""

        if not isinstance(soul, str):
            raise TypeError("soul must be text")
        observations = _freeze_records(context.observations)
        task_facts = _freeze_records(context.task_facts)
        memory = _freeze_records(memory_facts)
        tools = _freeze_records(allowed_tools)
        runtime = _freeze_mapping(runtime_state or {})
        conversation = _freeze_ordered_records(context.conversation_turns)
        sections = {
            "conversation": _clip_recent_json_records(conversation, self.budget.conversation_chars),
            "observations": _clip_json_records(observations, self.budget.observation_chars),
            "think_note": _clip(context.think_note, self.budget.observation_chars) if context.think_note else None,
            "task_facts": _clip_json_records(task_facts, self.budget.observation_chars),
            "memory_facts": _clip_json_records(memory, self.budget.memory_chars),
            "runtime_state": _clip_json_mapping(runtime, self.budget.runtime_chars),
            # Tool schemas are authority, not descriptive prompt data: partial exposure would make the
            # provider request disagree with its Snapshot.  The total budget below rejects if they cannot fit.
            "allowed_tool_surface": [_thaw(tool) for tool in tools],
        }
        sections = _fit_total_budget(sections, self.budget.max_prompt_chars)
        prompt = _canonical(sections)
        return Snapshot(
            transcript=_clip(context.transcript, self.budget.transcript_chars),
            delivered_clauses=tuple(_clip_sequence(context.delivered_clauses, self.budget.delivered_chars)),
            conversation_turns=tuple(_freeze_ordered_records(sections["conversation"])),
            soul=_clip(soul, self.budget.soul_chars), observations=tuple(_freeze_records(sections["observations"])),
            think_note=sections["think_note"],
            task_facts=tuple(_freeze_records(sections["task_facts"])),
            memory_facts=tuple(_freeze_records(sections["memory_facts"])),
            runtime_state=_freeze_mapping(sections["runtime_state"]),
            allowed_tool_surface=tuple(_freeze_records(sections["allowed_tool_surface"])), prompt=prompt,
        )


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("snapshot mappings must be mappings")
    if not all(isinstance(key, str) for key in value):
        raise ValueError("snapshot mapping keys must be strings")
    return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})


def _freeze_records(records: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze_mapping(record) for record in sorted(records, key=lambda item: _canonical(item)))


def _freeze_ordered_records(records: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(_freeze_mapping(record) for record in records)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("snapshot values must be finite JSON values")


def _canonical(value: Any) -> str:
    return json.dumps(_thaw(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _thaw(value: Any) -> Any:
    """Turn read-only snapshot containers back into JSON-native values for rendering only."""

    if isinstance(value, Mapping):
        return {str(key): _thaw(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _clip(text: str, limit: int) -> str:
    return text[:limit]


def _clip_sequence(values: Sequence[str], limit: int) -> list[str]:
    result: list[str] = []
    remaining = limit
    for value in values:
        if remaining <= 0:
            break
        clipped = _clip(str(value), remaining)
        if clipped:
            result.append(clipped)
            remaining -= len(clipped)
    return result


def _clip_json_records(records: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    remaining = limit
    for record in records:
        encoded = _canonical(record)
        if len(encoded) > remaining:
            break
        result.append(json.loads(encoded))
        remaining -= len(encoded)
    return result


def _clip_recent_json_records(records: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep the newest complete conversation turns while preserving their causal order."""

    result: list[dict[str, Any]] = []
    remaining = limit
    for record in reversed(records):
        encoded = _canonical(record)
        if len(encoded) > remaining:
            break
        result.append(json.loads(encoded))
        remaining -= len(encoded)
    result.reverse()
    return result


def _clip_json_mapping(mapping: Mapping[str, Any], limit: int) -> dict[str, Any]:
    encoded = _canonical(mapping)
    return json.loads(encoded) if len(encoded) <= limit else {}


def _fit_total_budget(sections: dict[str, Any], limit: int) -> dict[str, Any]:
    """Trim variable facts only; runtime/tool sections always remain explicit control structure."""

    projected = dict(sections)
    for key in ("memory_facts", "task_facts", "observations", "think_note", "conversation"):
        if len(_canonical(projected)) <= limit:
            return projected
        projected[key] = [] if isinstance(projected[key], list) else {} if isinstance(projected[key], dict) else ""
    if len(_canonical(projected)) > limit:
        raise ValueError("max_prompt_chars cannot fit required runtime and tool surface")
    return projected


__all__ = ["Snapshot", "SnapshotBudget", "SnapshotProjector"]
