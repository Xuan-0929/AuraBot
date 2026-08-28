"""Projects one profile's canonical facts into the bounded JSON payload consumed by control invokes.

ContextStore supplies ordered heard conversation; Runtime supplies current state, Memory, and the frozen tool
surface. The projector makes one JSON round-trip copy, keeps required runtime/tool authority, and evicts the
oldest complete variable facts until the payload fits. Soul remains a separate provider system message.
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
    soul_chars: int = 2_000
    conversation_chars: int = 6_000
    observation_chars: int = 3_000
    memory_chars: int = 2_400

    def __post_init__(self) -> None:
        if min(
            self.max_prompt_chars,
            self.soul_chars,
            self.conversation_chars,
            self.observation_chars,
            self.memory_chars,
        ) < 0 or self.max_prompt_chars < 2:
            raise ValueError("snapshot budgets must be non-negative and fit a JSON object")


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Independent JSON-native facts frozen for exactly one invoke."""

    conversation_turns: tuple[dict[str, Any], ...]
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
        """Copy JSON values, apply section limits, then evict oldest facts until within total budget."""

        if not isinstance(soul, str):
            raise TypeError("soul must be text")
        conversation = _recent(context.conversation_turns, self.budget.conversation_chars)
        observations = _records(context.observations, self.budget.observation_chars)
        memory = _records(memory_facts, self.budget.memory_chars)
        task_facts = _copy(context.task_facts)
        runtime = _object(runtime_state or {})
        tools = _copy(allowed_tools)
        sections: dict[str, Any] = {
            "conversation": conversation,
            "observations": observations,
            "think_note": context.think_note,
            "task_facts": task_facts,
            "memory_facts": memory,
            "runtime_state": runtime,
            "allowed_tool_surface": tools,
        }
        _fit(sections, self.budget.max_prompt_chars)
        prompt = _encode(sections)
        return Snapshot(
            conversation_turns=tuple(_copy(sections["conversation"])),
            soul=soul[: self.budget.soul_chars],
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
            break
        result.append(copied)
        used += size
    return result


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


def _fit(sections: dict[str, Any], limit: int) -> None:
    """Drop oldest complete facts while never trimming runtime state or the tool authority."""

    variable_lists = ("conversation", "memory_facts", "observations", "task_facts")
    while len(_encode(sections)) > limit:
        changed = False
        for key in variable_lists:
            values = sections[key]
            if values:
                values.pop(0)
                changed = True
                break
        if not changed and sections["think_note"]:
            sections["think_note"] = None
            changed = True
        if not changed:
            raise ValueError("max_prompt_chars cannot fit required runtime and tool surface")


__all__ = ["Snapshot", "SnapshotBudget", "SnapshotProjector"]
