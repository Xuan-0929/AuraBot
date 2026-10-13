"""Defines the Main Agent's concrete tools, effect groups, and frozen per-invoke capability surface.

The policy builds only tools backed by the active product composition, physically omits effects forbidden by
mode/grant state, and validates the small emitted JSON-Schema subset. ToolSurface retains immutable definition
records directly; it does not serialize and re-validate its own trusted construction on every invoke.
Cancellation schemas admit a Profile-owned proposal identity so the shared Task owner can resolve
the grace-window/commit race before delegating to the durable TaskManager.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from ..avatar import AvatarCapabilities


class InvokeMode(StrEnum):
    PREPARE = "PREPARE"
    RESPOND = "RESPOND"
    CONTINUE = "CONTINUE"


class EffectClass(StrEnum):
    SILENT = "SILENT"
    NOTE = "NOTE"
    SAY = "SAY"
    SAFE_READ = "SAFE_READ"
    DURABLE_ACTION = "DURABLE_ACTION"


@dataclass(frozen=True, slots=True)
class ResponseGrant:
    grant_id: str
    input_id: str
    revision: int
    context_version: int


@dataclass(frozen=True, slots=True)
class EffectFence:
    active_invoke_id: str
    input_id: str
    revision: int
    context_version: int
    response_grant: ResponseGrant | None = None
    speech_candidate: bool = False
    confirmed_speech: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    effect: EffectClass
    parameters: Mapping[str, Any]

    def provider_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": json.loads(json.dumps(self.parameters, ensure_ascii=False)),
            },
        }


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: Mapping[str, Any]
    index: int = 0
    raw_arguments: str = ""
    parse_error: str | None = None
    avatar: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    call_id: str
    tool_name: str
    accepted: bool
    code: str
    detail: str = ""


class ToolExecutorError(RuntimeError):
    def __init__(self, code: str) -> None:
        if not code or not code.replace("_", "").isalnum():
            raise ValueError("tool executor error code must be stable identifier text")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class StreamedSpeechReceipt:
    call_id: str
    text: str
    sequence: int


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    call_id: str
    tool_name: str
    observation_id: str
    result: Any = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class InvokeSettlement:
    invoke_id: str
    receipts: tuple[ToolReceipt, ...]
    streamed_speech: tuple[StreamedSpeechReceipt, ...] = ()
    cancelled: bool = False
    assistant_content_discarded: bool = False
    settled: bool = True


@dataclass(frozen=True, slots=True)
class ToolSurface:
    mode: InvokeMode
    input_id: str
    revision: int
    context_version: int
    response_grant: ResponseGrant | None
    speech_candidate: bool
    confirmed_speech: bool
    definitions: tuple[ToolDefinition, ...]
    avatar_capabilities: AvatarCapabilities = field(default_factory=AvatarCapabilities)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(definition.name for definition in self.definitions)

    def provider_schemas(self) -> tuple[dict[str, Any], ...]:
        return tuple(definition.provider_schema() for definition in self.definitions)

    def definition(self, name: str) -> ToolDefinition | None:
        return next((definition for definition in self.definitions if definition.name == name), None)


class ToolSurfacePolicy:
    """Build the fixed catalog once and freeze only currently authorized definitions per invoke."""

    def __init__(
        self,
        *,
        workspace_ids: Sequence[str] = (),
        task_controls: Sequence[str] = (),
        observation_tools: Sequence[str] = (),
        avatar_capabilities: AvatarCapabilities | None = None,
        max_calls: int = 8,
        max_durable_calls: int = 1,
        max_note_calls: int = 1,
    ) -> None:
        if min(max_calls, max_durable_calls, max_note_calls) < 1:
            raise ValueError("tool call limits must be positive")
        controls = tuple(dict.fromkeys(task_controls))
        observations = tuple(dict.fromkeys(observation_tools))
        if set(controls) - set(_TASK_CONTROLS):
            raise ValueError("unknown task control")
        if set(observations) - {"web_search", "web_read"}:
            raise ValueError("unknown observation tool")
        self.max_calls = max_calls
        self.max_durable_calls = max_durable_calls
        self.max_note_calls = max_note_calls
        self._definitions = _catalog(tuple(sorted(set(workspace_ids))), controls, observations)
        self.set_avatar_capabilities(avatar_capabilities or AvatarCapabilities())

    def set_avatar_capabilities(self, capabilities: AvatarCapabilities) -> None:
        self.avatar_capabilities = capabilities
        self._definitions = tuple(
            replace(definition, description=capabilities.description(), parameters=_object({
                "text": _text(maximum=8000), "avatar": capabilities.schema(),
            }, ("text",))) if definition.name == "say_to_user" else definition
            for definition in self._definitions
        )
        self._by_name = {definition.name: definition for definition in self._definitions}

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def definition(self, name: str) -> ToolDefinition | None:
        return self._by_name.get(name)

    def freeze_surface(
        self,
        mode: InvokeMode,
        *,
        input_id: str,
        revision: int,
        context_version: int,
        response_grant: ResponseGrant | None = None,
        current_response_grant: ResponseGrant | None = None,
        speech_candidate: bool = False,
        confirmed_speech: bool = False,
    ) -> ToolSurface:
        selected = InvokeMode(mode)
        effects = {EffectClass.SAFE_READ}
        if selected is InvokeMode.PREPARE:
            effects.add(EffectClass.NOTE)
        valid_grant = (
            response_grant is not None
            and response_grant == current_response_grant
            and response_grant.input_id == input_id
            and response_grant.revision == revision
            and response_grant.context_version == context_version
        )
        if selected is not InvokeMode.PREPARE and valid_grant and not speech_candidate and not confirmed_speech:
            effects.update((EffectClass.SAY, EffectClass.DURABLE_ACTION))
        return ToolSurface(
            selected,
            input_id,
            revision,
            context_version,
            response_grant if valid_grant and selected is not InvokeMode.PREPARE else None,
            speech_candidate,
            confirmed_speech,
            tuple(definition for definition in self._definitions if definition.effect in effects),
            self.avatar_capabilities,
        )

    def validate_arguments(self, call: ToolCall, surface: ToolSurface) -> str | None:
        if call.parse_error:
            return call.parse_error
        definition = surface.definition(call.name)
        if definition is None:
            return "tool_not_exposed"
        if call.name == "say_to_user":
            if not isinstance(call.arguments, Mapping) or set(call.arguments) - {"text", "avatar"}:
                return "schema_invalid"
            return None if _matches({"text": call.arguments.get("text")}, _object({"text": _text(maximum=8000)}, ("text",))) else "schema_invalid"
        return None if _matches(call.arguments, definition.parameters) else "schema_invalid"


def _object(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required), "additionalProperties": False}


def _text(*, minimum: int = 1, maximum: int = 8_000, enum: Sequence[str] = ()) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if enum:
        value["enum"] = list(enum)
    return value


_TASK_CONTROLS = ("inspect_tasks", "answer_task_question", "pause_task", "resume_task", "cancel_task", "delete_task", "rerun_task")


def _catalog(
    workspace_ids: tuple[str, ...], task_controls: tuple[str, ...], observation_tools: tuple[str, ...]
) -> tuple[ToolDefinition, ...]:
    definitions = [
        ToolDefinition(
            "say_to_user", "Speak user-visible text.", EffectClass.SAY,
            _object({"text": _text(maximum=8_000)}, ("text",)),
        ),
        ToolDefinition(
            "note_think_checkpoint", "Save one short PREPARE checkpoint.", EffectClass.NOTE,
            _object({
                "current_understanding": _text(maximum=1_000),
                "unresolved_questions": {"type": "array", "items": _text(maximum=300), "maxItems": 8},
                "useful_known_facts": {"type": "array", "items": _text(maximum=500), "maxItems": 12},
                "recommended_next_step": _text(maximum=500),
            }, ("current_understanding",)),
        ),
    ]
    web = {
        "web_search": ToolDefinition(
            "web_search", "Search the public web.", EffectClass.SAFE_READ,
            _object({"query": _text(maximum=1_000)}, ("query",)),
        ),
        "web_read": ToolDefinition(
            "web_read", "Extract a prior search result.", EffectClass.SAFE_READ,
            _object({"source_id": _text(maximum=128)}, ("source_id",)),
        ),
    }
    definitions.extend(web[name] for name in observation_tools)
    if workspace_ids:
        workspace = _text(maximum=64, enum=workspace_ids)
        definitions.extend((
            ToolDefinition(
                "workspace_list", "List one workspace directory level.", EffectClass.SAFE_READ,
                _object({"workspace_id": workspace, "path": _text(minimum=0, maximum=1_024)}, ("workspace_id",)),
            ),
            ToolDefinition(
                "workspace_read", "Read a bounded workspace text page.", EffectClass.SAFE_READ,
                _object({
                    "workspace_id": workspace,
                    "path": _text(maximum=1_024),
                    "start_line": {"type": "integer", "minimum": 1, "maximum": 10_000},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
                }, ("workspace_id", "path")),
            ),
            ToolDefinition(
                "task_assign", "Create a three-second Task proposal.", EffectClass.DURABLE_ACTION,
                _object({
                    "title": _text(maximum=200),
                    "prompt": _text(maximum=200_000),
                    "workspace_id": workspace,
                }, ("title", "prompt", "workspace_id")),
            ),
        ))
    task_id = _text(maximum=36)
    task = {
        "inspect_tasks": ToolDefinition(
            "inspect_tasks", "Inspect active-profile Tasks.", EffectClass.SAFE_READ,
            _object({
                "statuses": {"type": "array", "items": _text(maximum=16, enum=(
                    "queued", "running", "waiting", "paused", "completed", "failed", "cancelled",
                )), "maxItems": 7},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            }),
        ),
        "answer_task_question": ToolDefinition(
            "answer_task_question", "Answer an owned Task question.", EffectClass.DURABLE_ACTION,
            _object({"task_id": task_id, "question_id": _text(maximum=36), "answer": _text()},
                    ("task_id", "question_id", "answer")),
        ),
        "pause_task": ToolDefinition("pause_task", "Pause an owned Task.", EffectClass.DURABLE_ACTION,
                                      _object({"task_id": task_id}, ("task_id",))),
        "resume_task": ToolDefinition("resume_task", "Resume an owned Task.", EffectClass.DURABLE_ACTION,
                                       _object({"task_id": task_id}, ("task_id",))),
        "cancel_task": ToolDefinition("cancel_task", "Cancel an owned Task or pending proposal using its task_id or proposal_id as task_id.", EffectClass.DURABLE_ACTION,
                                       _object({"task_id": _text(maximum=128)}, ("task_id",))),
    }
    for name, description in (("delete_task", "Delete an owned terminal Task."), ("rerun_task", "Rerun an owned terminal Task as a new Task.")):
        task[name] = ToolDefinition(name, description, EffectClass.DURABLE_ACTION, _object({"task_id": task_id}, ("task_id",)))
    definitions.extend(task[name] for name in _TASK_CONTROLS if name in task_controls)
    return tuple(definitions)


def _matches(value: Any, schema: Mapping[str, Any]) -> bool:
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            return False
        properties = schema.get("properties", {})
        return (
            not (schema.get("additionalProperties") is False and any(key not in properties for key in value))
            and all(key in value for key in schema.get("required", ()))
            and all(_matches(item, properties[key]) for key, item in value.items())
        )
    if expected == "array":
        return isinstance(value, list) and len(value) <= schema.get("maxItems", len(value)) and all(
            _matches(item, schema["items"]) for item in value
        )
    if expected == "string":
        return (
            isinstance(value, str)
            and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
            and schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value))
            and ("enum" not in schema or value in schema["enum"])
        )
    if expected == "integer":
        return type(value) is int and schema.get("minimum", value) <= value <= schema.get("maximum", value)
    return False


__all__ = [
    "EffectClass", "EffectFence", "InvokeMode", "InvokeSettlement", "ObservationOutcome", "ResponseGrant",
    "StreamedSpeechReceipt", "ToolCall", "ToolDefinition", "ToolExecutorError", "ToolReceipt", "ToolSurface",
    "ToolSurfacePolicy",
]
