"""Define the Main Agent's approved tool catalog, effect model, and dynamic exposure policy.

This module is the deterministic trust boundary between an immutable invoke snapshot and provider
tool schemas.  It physically omits effects unavailable to an invoke mode, builds workspace/avatar
enums from trusted runtime facts, and exposes small immutable records shared by the stream parser,
ToolHarness, and the later scheduler/adapters.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class InvokeMode(StrEnum):
    """One of the three scheduler-owned control invoke modes."""

    PREPARE = "PREPARE"
    RESPOND = "RESPOND"
    CONTINUE = "CONTINUE"


class EffectClass(StrEnum):
    """Monotonic externally relevant effect classes used for group policy."""

    SILENT = "SILENT"
    NOTE = "NOTE"
    SAY = "SAY"
    SAFE_READ = "SAFE_READ"
    DURABLE_ACTION = "DURABLE_ACTION"


@dataclass(frozen=True, slots=True)
class ResponseGrant:
    """Scheduler capability tying response effects to one input revision and Context version."""

    grant_id: str
    input_id: str
    revision: int
    context_version: int


@dataclass(frozen=True, slots=True)
class EffectFence:
    """Latest scheduler facts re-read immediately before accepting an effect."""

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
    """One approved provider tool and its runtime effect classification."""

    name: str
    description: str
    effect: EffectClass
    parameters: Mapping[str, Any]

    def provider_schema(self) -> dict[str, Any]:
        """Return a fresh OpenAI-compatible function schema safe for snapshot projection."""

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
    """A completely assembled provider call; parse_error keeps malformed calls receiptable."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]
    index: int = 0
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass(frozen=True, slots=True)
class ToolReceipt:
    """Stable audit result for an accepted, detached, discarded, or rejected provider effect."""

    call_id: str
    tool_name: str
    accepted: bool
    code: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class StreamedSpeechReceipt:
    """Truth that one clause was already accepted even if its enclosing call later fails."""

    call_id: str
    text: str
    sequence: int


@dataclass(frozen=True, slots=True)
class ObservationOutcome:
    """Retained completion of detached Observation work for its later ledger owner."""

    call_id: str
    tool_name: str
    result: Any = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class InvokeSettlement:
    """Terminal control lifecycle result; detached Observation work is intentionally excluded."""

    invoke_id: str
    receipts: tuple[ToolReceipt, ...]
    streamed_speech: tuple[StreamedSpeechReceipt, ...] = ()
    cancelled: bool = False
    assistant_content_discarded: bool = False
    settled: bool = True


@dataclass(frozen=True, slots=True)
class ToolSurface:
    """Exact immutable provider capability frozen alongside one Task-2 invoke Snapshot."""

    mode: InvokeMode
    input_id: str
    revision: int
    context_version: int
    response_grant: ResponseGrant | None
    speech_candidate: bool
    confirmed_speech: bool
    schema_records: tuple[tuple[str, str], ...]

    @property
    def names(self) -> frozenset[str]:
        """Return the exact physically exposed function names."""

        return frozenset(name for name, _ in self.schema_records)

    def provider_schemas(self) -> tuple[dict[str, Any], ...]:
        """Return fresh mappings for Snapshot projection without exposing mutable internal state."""

        return tuple(json.loads(encoded) for _, encoded in self.schema_records)

    def parameters_for(self, name: str) -> Mapping[str, Any] | None:
        """Return the exact frozen parameter schema for one exposed name."""

        for current, encoded in self.schema_records:
            if current == name:
                return json.loads(encoded)["function"]["parameters"]
        return None


def _object(properties: Mapping[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def _text(*, minimum: int = 1, maximum: int = 8_000, enum: Sequence[str] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "minLength": minimum, "maxLength": maximum}
    if enum:
        schema["enum"] = list(enum)
    return schema


class ToolSurfacePolicy:
    """Build mode/grant-sensitive schemas and validate calls against the same approved catalog."""

    def __init__(
        self,
        *,
        workspace_ids: Sequence[str] = (),
        motion_tokens: Sequence[str] = (),
        expression_tokens: Sequence[str] = (),
        max_calls: int = 8,
        max_durable_calls: int = 1,
        max_note_calls: int = 1,
    ) -> None:
        if max_calls < 1 or max_durable_calls < 1 or max_note_calls < 1:
            raise ValueError("tool call limits must be positive")
        self.max_calls = max_calls
        self.max_durable_calls = max_durable_calls
        self.max_note_calls = max_note_calls
        self._definitions = _approved_definitions(
            tuple(sorted(set(workspace_ids))),
            tuple(sorted(set(motion_tokens))),
            tuple(sorted(set(expression_tokens))),
        )

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return the complete approved catalog; callers must still apply invoke exposure."""

        return tuple(self._definitions.values())

    def definition(self, name: str) -> ToolDefinition | None:
        """Look up only a registered tool name."""

        return self._definitions.get(name)

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
        """Freeze only schemas authorized by exact grant coordinates and current identity."""

        selected_mode = InvokeMode(mode)
        allowed_effects = {EffectClass.SAFE_READ}
        if selected_mode is InvokeMode.PREPARE:
            allowed_effects.add(EffectClass.NOTE)
        grant_valid = (
            response_grant is not None
            and response_grant == current_response_grant
            and response_grant.input_id == input_id
            and response_grant.revision == revision
            and response_grant.context_version == context_version
        )
        if selected_mode is not InvokeMode.PREPARE and grant_valid and not speech_candidate and not confirmed_speech:
            allowed_effects.update((EffectClass.SAY, EffectClass.DURABLE_ACTION))
        schemas = tuple(
            (definition.name, json.dumps(
                definition.provider_schema(), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ))
            for definition in self._definitions.values()
            if definition.effect in allowed_effects
        )
        return ToolSurface(
            selected_mode, input_id, revision, context_version,
            response_grant if grant_valid and selected_mode is not InvokeMode.PREPARE else None,
            speech_candidate, confirmed_speech, schemas,
        )

    def validates_surface(self, surface: object) -> bool:
        """Reject mutated or caller-fabricated capabilities before provider output is consumed."""

        if not isinstance(surface, ToolSurface):
            return False
        if (
            not isinstance(surface.mode, InvokeMode)
            or not isinstance(surface.input_id, str) or not surface.input_id
            or type(surface.revision) is not int or surface.revision < 0
            or type(surface.context_version) is not int or surface.context_version < 0
            or type(surface.speech_candidate) is not bool
            or type(surface.confirmed_speech) is not bool
            or not isinstance(surface.schema_records, tuple)
            or not _valid_grant(surface.response_grant)
        ):
            return False
        try:
            for record in surface.schema_records:
                if (
                    not isinstance(record, tuple) or len(record) != 2
                    or not all(isinstance(item, str) for item in record)
                    or not isinstance(json.loads(record[1]), dict)
                ):
                    return False
            expected = self.freeze_surface(
                surface.mode, input_id=surface.input_id, revision=surface.revision,
                context_version=surface.context_version, response_grant=surface.response_grant,
                current_response_grant=surface.response_grant,
                speech_candidate=surface.speech_candidate,
                confirmed_speech=surface.confirmed_speech,
            )
        except Exception:
            return False
        return expected == surface

    def validate_arguments(self, call: ToolCall, surface: ToolSurface) -> str | None:
        """Return a stable rejection code for malformed/invalid arguments, otherwise None."""

        if call.parse_error:
            return call.parse_error
        schema = surface.parameters_for(call.name)
        if schema is None:
            return "tool_not_exposed"
        return None if _matches_schema(call.arguments, schema) else "schema_invalid"


def _valid_grant(grant: ResponseGrant | None) -> bool:
    return grant is None or (
        isinstance(grant, ResponseGrant)
        and isinstance(grant.grant_id, str) and bool(grant.grant_id)
        and isinstance(grant.input_id, str) and bool(grant.input_id)
        and type(grant.revision) is int and grant.revision >= 0
        and type(grant.context_version) is int and grant.context_version >= 0
    )


def _approved_definitions(
    workspace_ids: tuple[str, ...], motion_tokens: tuple[str, ...], expression_tokens: tuple[str, ...]
) -> dict[str, ToolDefinition]:
    """Construct the fixed catalog while injecting only trusted dynamic enum values."""

    say_properties = {"text": _text(maximum=8_000)}
    if motion_tokens:
        say_properties["motion_token"] = _text(maximum=64, enum=motion_tokens)
    if expression_tokens:
        say_properties["expression_token"] = _text(maximum=64, enum=expression_tokens)
    definitions = [
        ToolDefinition(
            "say_to_user", "Offer user-visible speech to the clause delivery pipeline.", EffectClass.SAY,
            _object(say_properties, ("text",)),
        ),
        ToolDefinition(
            "note_think_checkpoint", "Save a short conclusion-oriented PREPARE checkpoint.", EffectClass.NOTE,
            _object({
                "current_understanding": _text(maximum=1_000),
                "unresolved_questions": {"type": "array", "items": _text(maximum=300), "maxItems": 8},
                "useful_known_facts": {"type": "array", "items": _text(maximum=500), "maxItems": 12},
                "recommended_next_step": _text(maximum=500),
            }, ("current_understanding",)),
        ),
        ToolDefinition(
            "web_search", "Start a safe web search Observation.", EffectClass.SAFE_READ,
            _object({"query": _text(maximum=1_000)}, ("query",)),
        ),
        ToolDefinition(
            "web_read", "Read a previously registered public web source.", EffectClass.SAFE_READ,
            _object({"source_id": _text(maximum=128)}, ("source_id",)),
        ),
    ]
    if workspace_ids:
        workspace = _text(maximum=64, enum=workspace_ids)
        definitions.extend((
            ToolDefinition(
                "workspace_list", "Inspect one level of a registered workspace directory.", EffectClass.SAFE_READ,
                _object({"workspace_id": workspace, "path": _text(minimum=0, maximum=1_024)},
                        ("workspace_id",)),
            ),
            ToolDefinition(
                "workspace_read", "Read a bounded page from a registered workspace text file.", EffectClass.SAFE_READ,
                _object({
                    "workspace_id": workspace, "path": _text(maximum=1_024),
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": "integer", "minimum": 1, "maximum": 200},
                }, ("workspace_id", "path")),
            ),
            ToolDefinition(
                "task_assign", "Create a cancellable pending durable Task proposal.", EffectClass.DURABLE_ACTION,
                _object({
                    "title": _text(maximum=200), "prompt": _text(maximum=200_000),
                    "workspace_id": workspace,
                }, ("title", "prompt", "workspace_id")),
            ),
        ))
    return {definition.name: definition for definition in definitions}


def _matches_schema(value: Any, schema: Mapping[str, Any]) -> bool:
    """Validate the small JSON-Schema subset emitted above without adding a runtime dependency."""

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and any(key not in properties for key in value):
            return False
        if any(key not in value for key in schema.get("required", ())):
            return False
        return all(_matches_schema(item, properties[key]) for key, item in value.items())
    if expected == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", len(value)):
            return False
        return all(_matches_schema(item, schema["items"]) for item in value)
    if expected == "string":
        if not isinstance(value, str):
            return False
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            return False
        if not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", len(value)):
            return False
        return "enum" not in schema or value in schema["enum"]
    if expected == "integer":
        return (
            isinstance(value, int) and not isinstance(value, bool)
            and value >= schema.get("minimum", value) and value <= schema.get("maximum", value)
        )
    return False


__all__ = [
    "EffectClass", "EffectFence", "InvokeMode", "InvokeSettlement", "ObservationOutcome",
    "ResponseGrant", "StreamedSpeechReceipt", "ToolCall", "ToolDefinition", "ToolReceipt",
    "ToolSurface", "ToolSurfacePolicy",
]
