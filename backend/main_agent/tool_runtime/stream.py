"""Assemble provider tool deltas and incrementally decode ordered ``say_to_user`` fields.

Provider adapters may split call ids, names, JSON arguments, escape sequences, and Unicode surrogate
pairs at arbitrary boundaries.  This module normalizes common Chat Completions/Responses shapes into
ordered ToolCall records while a tiny state machine emits natural, 24-codepoint soft, or 80-codepoint
hard speech clauses; ToolHarness still validates every complete call and owns the final-tail flush.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .definitions import ToolCall


_CLAUSE_END = frozenset("。！？!?；;\n")
_SOFT_END = frozenset("，,、：:—- \t")
_SOFT_LENGTH = 24
_HARD_LENGTH = 80
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                   "n": "\n", "r": "\r", "t": "\t"}


class SayTextStream:
    """Incrementally decode the top-level text string and yield only stable complete clauses."""

    def __init__(self, *, max_length: int = 8_000) -> None:
        if max_length < 1:
            raise ValueError("max_length must be positive")
        self.max_length = max_length
        self._raw = ""
        self._position: int | None = None
        self._decoded = ""
        self._emitted = 0
        self._closed = False
        self.invalid = False
        self.error_code: str | None = None
        self.motion_token: str | None = None
        self.expression_token: str | None = None

    @property
    def complete_text(self) -> str:
        """Return all decoded text available so far, including the un-emitted final tail."""

        return self._decoded

    @property
    def remaining_text(self) -> str:
        """Return the tail not yet emitted as a punctuation-stable clause."""

        return self._decoded[self._emitted:]

    @property
    def emitted_length(self) -> int:
        """Count decoded code points already handed to the speech executor."""

        return self._emitted

    def feed(self, fragment: str) -> tuple[str, ...]:
        """Consume one raw arguments fragment and return newly stable decoded clauses."""

        if not isinstance(fragment, str):
            raise TypeError("tool argument fragments must be strings")
        self._raw += fragment
        if self._position is None:
            try:
                located = _locate_text_string(self._raw)
            except ValueError:
                self._reject("malformed_arguments")
                return ()
            if located is None:
                return ()
            self._position, metadata = located
            self.motion_token = metadata.get("motion_token")
            self.expression_token = metadata.get("expression_token")
        clauses: list[str] = []
        while not self._closed and self._position < len(self._raw):
            position = self._position
            character = self._raw[position]
            if character == '"':
                self._closed = True
                self._position += 1
                break
            if character != "\\":
                if 0xD800 <= ord(character) <= 0xDFFF:
                    self._reject("invalid_unicode_surrogate")
                    break
                if ord(character) < 0x20:
                    self._reject("malformed_arguments")
                    break
                if len(self._decoded) >= self.max_length:
                    self._reject("schema_invalid")
                    break
                self._decoded += character
                self._position += 1
            else:
                decoded = self._decode_escape(position)
                if decoded is None:
                    break
                value, consumed = decoded
                if len(self._decoded) + len(value) > self.max_length:
                    self._reject("schema_invalid")
                    break
                self._decoded += value
                self._position += consumed
            clauses.extend(self._ready_clauses())
        return () if self.invalid else tuple(clauses)

    def _ready_clauses(self) -> tuple[str, ...]:
        """Cut every boundary made stable by decoded text while retaining a short unfinished tail."""

        clauses: list[str] = []
        while tail := self.remaining_text:
            length = _next_clause_length(tail)
            if length is None:
                break
            clauses.append(tail[:length])
            self._emitted += length
        return tuple(clauses)

    def _reject(self, code: str) -> None:
        self.invalid = True
        self.error_code = code
        self._closed = True

    def _decode_escape(self, position: int) -> tuple[str, int] | None:
        if position + 1 >= len(self._raw):
            return None
        marker = self._raw[position + 1]
        if marker in _SIMPLE_ESCAPES:
            return _SIMPLE_ESCAPES[marker], 2
        if marker != "u":
            # Full JSON validation will reject this call; do not expose malformed speech.
            self._reject("malformed_arguments")
            return "", 2
        if position + 6 > len(self._raw):
            return None
        digits = self._raw[position + 2:position + 6]
        if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
            self._reject("malformed_arguments")
            return "", 6
        codepoint = int(digits, 16)
        if 0xD800 <= codepoint <= 0xDBFF:
            if position + 12 > len(self._raw):
                return None
            suffix = self._raw[position + 6:position + 12]
            if re.fullmatch(r"\\u[0-9a-fA-F]{4}", suffix):
                low = int(suffix[2:], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    return chr(0x10000 + ((codepoint - 0xD800) << 10) + low - 0xDC00), 12
            self._reject("invalid_unicode_surrogate")
            return "", 6
        if 0xDC00 <= codepoint <= 0xDFFF:
            self._reject("invalid_unicode_surrogate")
            return "", 6
        return chr(codepoint), 6


@dataclass(frozen=True, slots=True)
class SayClause:
    """A stable speech fragment tied to its provider call identity and order."""

    index: int
    call_id: str
    text: str
    motion_token: str | None = None
    expression_token: str | None = None


@dataclass(slots=True)
class _PartialCall:
    index: int
    call_id: str = ""
    identity_complete: bool = False
    name: str = ""
    name_complete: bool = False
    raw_arguments: str = ""
    say_stream: SayTextStream | None = None
    parse_error: str | None = None
    pending_clauses: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _CallDelta:
    index: int
    identity: str = ""
    identity_kind: str = "none"
    name: str = ""
    name_kind: str = "complete"
    arguments: str = ""
    mark_name_complete: bool = False


class ToolCallAssembler:
    """Normalize and assemble interleaved calls while retaining malformed calls for receipts."""

    def __init__(self) -> None:
        self._calls: dict[int, _PartialCall] = {}
        self._external_indexes: dict[str, int] = {}
        self._response_item_indexes: dict[str, int] = {}
        self.assistant_content_seen = False
        self.protocol_error: str | None = None

    def feed(self, chunk: Mapping[str, Any]) -> tuple[SayClause, ...]:
        """Consume one provider chunk and return newly decoded stable say clauses."""

        if not isinstance(chunk, Mapping):
            raise TypeError("provider chunks must be mappings")
        try:
            deltas, has_content = self._normalize(chunk)
        except ValueError:
            self.protocol_error = "provider_stream_error"
            return ()
        self.assistant_content_seen = self.assistant_content_seen or has_content
        clauses: list[SayClause] = []
        for item in deltas:
            index, arguments = item.index, item.arguments
            partial = self._calls.setdefault(index, _PartialCall(index))
            self._merge_identity(partial, item.identity, item.identity_kind)
            self._merge_name(partial, item.name, item.name_kind, item.mark_name_complete)
            if partial.parse_error:
                partial.raw_arguments += arguments
                continue
            if partial.name == "say_to_user" and partial.say_stream is None:
                partial.say_stream = SayTextStream()
                if partial.raw_arguments:
                    partial.pending_clauses.extend(partial.say_stream.feed(partial.raw_arguments))
            partial.raw_arguments += arguments
            if partial.say_stream is not None and arguments:
                partial.pending_clauses.extend(partial.say_stream.feed(arguments))
                if partial.say_stream.invalid:
                    partial.pending_clauses.clear()
        if self.protocol_error:
            return ()
        for index in sorted(self._calls):
            partial = self._calls[index]
            stream = partial.say_stream
            clauses.extend(
                SayClause(
                    index,
                    partial.call_id or f"call_{index}",
                    text,
                    stream.motion_token if stream else None,
                    stream.expression_token if stream else None,
                )
                for text in partial.pending_clauses
            )
        return tuple(clauses)

    def accept_say_clause(self, index: int, text: str) -> None:
        """Advance decoded-prefix ownership only after the speech executor accepts the clause."""

        partial = self._calls[index]
        if not partial.pending_clauses or partial.pending_clauses[0] != text:
            raise ValueError("say clause acceptance is out of order")
        partial.pending_clauses.pop(0)

    def can_stream_say(self, index: int, max_calls: int) -> bool:
        """Require a reserved slot, complete identity/name, and all lower provider slots."""

        partial = self._calls.get(index)
        if (
            partial is None or index >= max_calls or partial.parse_error
            or self.protocol_error or not partial.identity_complete
            or not partial.name_complete or partial.name != "say_to_user"
        ):
            return False
        return all(
            lower in self._calls
            and self._calls[lower].identity_complete
            and self._calls[lower].name_complete
            and bool(self._calls[lower].name)
            and self._calls[lower].name != "say_to_user"
            and not self._calls[lower].parse_error
            for lower in range(index)
        )

    def finish(self) -> tuple[ToolCall, ...]:
        """Parse every complete argument object without dropping malformed or fabricated calls."""

        calls: list[ToolCall] = []
        for index in sorted(self._calls):
            partial = self._calls[index]
            arguments: Mapping[str, Any] = {}
            error = partial.parse_error
            try:
                parsed = _load_json_object(partial.raw_arguments)
                if not isinstance(parsed, dict):
                    raise ValueError("tool arguments must be an object")
                arguments = parsed
                if _contains_surrogate(arguments):
                    error = error or "invalid_unicode_surrogate"
            except _DuplicateKeyError:
                error = error or "duplicate_json_key"
            except (json.JSONDecodeError, TypeError, ValueError):
                error = error or "malformed_arguments"
            if partial.say_stream is not None and partial.say_stream.invalid:
                error = error or partial.say_stream.error_code or "malformed_arguments"
            if not partial.identity_complete:
                error = error or "ambiguous_call_identity"
            if not partial.name_complete or not partial.name:
                error = error or "conflicting_tool_name"
            if self.protocol_error:
                error = "provider_stream_error"
            calls.append(ToolCall(
                call_id=partial.call_id or f"call_{index}", name=partial.name,
                arguments=arguments, index=index, raw_arguments=partial.raw_arguments,
                parse_error=error,
            ))
        return tuple(calls)

    def say_stream(self, index: int) -> SayTextStream | None:
        """Return the incremental speech decoder for a completed say call, if one exists."""

        partial = self._calls.get(index)
        return partial.say_stream if partial else None

    def _index_for(self, external_id: str | None, suggested: Any, *, require_existing: bool = False) -> int:
        if require_existing:
            if external_id and external_id in self._external_indexes:
                index = self._external_indexes[external_id]
                if isinstance(suggested, int) and suggested >= 0 and suggested != index:
                    raise ValueError("conflicting provider call index")
                return index
            if not external_id and isinstance(suggested, int) and suggested in self._calls:
                return suggested
            raise ValueError("unknown provider call identity")
        if isinstance(suggested, int) and suggested >= 0:
            if external_id:
                prior = self._external_indexes.get(external_id)
                if prior is None:
                    self._external_indexes[external_id] = suggested
            return suggested
        if external_id and external_id in self._external_indexes:
            return self._external_indexes[external_id]
        index = max(self._calls, default=-1) + 1
        if external_id:
            self._external_indexes[external_id] = index
        return index

    def _response_index_for(self, item_id: str, suggested: Any, *, require_existing: bool) -> int:
        if (
            not item_id or not isinstance(suggested, int) or isinstance(suggested, bool)
            or suggested < 0
        ):
            raise ValueError("invalid Responses item identity")
        prior = self._response_item_indexes.get(item_id)
        if require_existing:
            if prior is None or prior != suggested:
                raise ValueError("unknown Responses item identity")
            return prior
        if prior is not None:
            if prior != suggested:
                raise ValueError("conflicting Responses item index")
            return prior
        if suggested in self._response_item_indexes.values():
            raise ValueError("conflicting Responses item identity")
        self._response_item_indexes[item_id] = suggested
        return suggested

    def _normalize(self, chunk: Mapping[str, Any]) -> tuple[list[_CallDelta], bool]:
        deltas: list[_CallDelta] = []
        content = False
        choices = chunk.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                delta = choice.get("delta", {}) if isinstance(choice, Mapping) else {}
                if isinstance(delta, Mapping) and delta.get("content") not in (None, ""):
                    content = True
                tool_calls = delta.get("tool_calls", ()) if isinstance(delta, Mapping) else ()
                if isinstance(tool_calls, list):
                    for item in tool_calls:
                        if not isinstance(item, Mapping):
                            continue
                        function = item.get("function", {})
                        function = function if isinstance(function, Mapping) else {}
                        call_id = item.get("id") if isinstance(item.get("id"), str) else ""
                        index = self._index_for(call_id or None, item.get("index"))
                        deltas.append(_CallDelta(
                            index, call_id, "complete" if call_id else "none",
                            _string(function.get("name")), "complete",
                            _string(function.get("arguments")),
                            bool(_string(function.get("name"))),
                        ))
        event_type = chunk.get("type")
        if event_type in {"response.output_text.delta", "response.output_text.done"}:
            content = bool(chunk.get("delta") or chunk.get("text"))
        elif event_type == "response.output_item.added":
            item = chunk.get("item", {})
            if isinstance(item, Mapping) and item.get("type") == "function_call":
                external = _string(item.get("id"))
                index = self._response_index_for(
                    external, chunk.get("output_index"),
                    require_existing=external in self._response_item_indexes,
                )
                identity = _string(item.get("call_id"))
                deltas.append(_CallDelta(
                    index, identity, "complete", _string(item.get("name")), "complete",
                    _string(item.get("arguments")),
                    bool(_string(item.get("name"))),
                ))
        elif event_type == "response.function_call_arguments.delta":
            external = _string(chunk.get("item_id"))
            arguments_delta = chunk.get("delta")
            if not isinstance(arguments_delta, str):
                raise ValueError("invalid Responses arguments delta")
            index = self._response_index_for(
                external, chunk.get("output_index"), require_existing=True,
            )
            deltas.append(_CallDelta(index, "", "none", "", "complete", arguments_delta))
        elif event_type == "response.output_item.done":
            item = chunk.get("item", {})
            if not isinstance(item, Mapping):
                raise ValueError("invalid Responses terminal item")
            external = _string(item.get("id"))
            index = self._response_index_for(
                external, chunk.get("output_index"), require_existing=True,
            )
            existing = self._calls.get(index)
            if existing is None:
                raise ValueError("unknown Responses item call")
            identity = item.get("call_id")
            terminal_name = item.get("name")
            terminal_arguments = item.get("arguments")
            if (
                item.get("type") != "function_call"
                or not isinstance(identity, str) or not identity
                or not isinstance(terminal_name, str) or not terminal_name
                or not isinstance(terminal_arguments, str) or not terminal_arguments
            ):
                raise ValueError("invalid Responses terminal call")
            if existing.call_id != identity:
                existing.parse_error = existing.parse_error or "ambiguous_call_identity"
            if existing.name != terminal_name:
                existing.parse_error = existing.parse_error or "conflicting_tool_name"
            if existing.raw_arguments != terminal_arguments:
                existing.parse_error = existing.parse_error or "conflicting_arguments"
            deltas.append(_CallDelta(
                index, identity, "complete", terminal_name, "complete", "", True,
            ))
        direct = chunk.get("tool_call")
        if isinstance(direct, Mapping):
            fragment = _string(direct.get("call_id_delta"))
            call_id = _string(direct.get("call_id") or direct.get("id"))
            identity = fragment or call_id
            identity_kind = "fragment" if fragment else "complete" if call_id else "none"
            index = self._index_for(call_id or None, direct.get("index"))
            name_fragment = _string(direct.get("name_delta"))
            name = name_fragment or _string(direct.get("name"))
            deltas.append(_CallDelta(
                index, identity, identity_kind, name,
                "fragment" if name_fragment else "complete",
                _string(direct.get("arguments_delta") or direct.get("arguments")),
                direct.get("name_complete") is True or bool(name and not name_fragment),
            ))
            if direct.get("identity_complete") is True:
                deltas.append(_CallDelta(index, "", "mark_complete"))
        direct_calls = chunk.get("tool_calls")
        if isinstance(direct_calls, list):
            for item in direct_calls:
                if not isinstance(item, Mapping):
                    continue
                function = item.get("function", item)
                function = function if isinstance(function, Mapping) else {}
                call_id = _string(item.get("call_id") or item.get("id"))
                index = self._index_for(call_id or None, item.get("index"))
                deltas.append(_CallDelta(
                    index, call_id, "complete" if call_id else "none",
                    _string(function.get("name")), "complete",
                    _string(function.get("arguments_delta") or function.get("arguments")),
                    bool(_string(function.get("name"))),
                ))
        if chunk.get("content") not in (None, ""):
            content = True
        return deltas, content

    def _merge_identity(self, partial: _PartialCall, value: str, kind: str) -> None:
        if kind == "fragment":
            if partial.identity_complete:
                partial.parse_error = partial.parse_error or "ambiguous_call_identity"
                return
            partial.call_id += value
            return
        if kind == "mark_complete":
            if not partial.call_id:
                partial.parse_error = partial.parse_error or "ambiguous_call_identity"
                return
            partial.identity_complete = True
            self._reserve_identity(partial)
            return
        if kind == "complete" and value:
            if partial.call_id and partial.call_id != value:
                partial.parse_error = partial.parse_error or "ambiguous_call_identity"
                return
            partial.call_id = value
            partial.identity_complete = True
            self._reserve_identity(partial)

    def _reserve_identity(self, partial: _PartialCall) -> None:
        prior = self._external_indexes.get(partial.call_id)
        if prior is not None and prior != partial.index:
            partial.parse_error = "ambiguous_call_identity"
            other = self._calls.get(prior)
            if other is not None:
                other.parse_error = "ambiguous_call_identity"
            return
        self._external_indexes[partial.call_id] = partial.index

    @staticmethod
    def _merge_name(partial: _PartialCall, value: str, kind: str, mark_complete: bool) -> None:
        if not value:
            return
        if kind == "fragment" and not partial.name:
            partial.name = value
        elif kind == "fragment":
            partial.name += value
        elif not partial.name:
            partial.name = value
        elif partial.name != value:
            partial.parse_error = partial.parse_error or "conflicting_tool_name"
        if mark_complete:
            partial.name_complete = True


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def segment_say_text(text: str, *, final: bool) -> tuple[str, ...]:
    """Segment decoded speech at natural/soft/hard boundaries and optionally flush its final tail."""

    if not isinstance(text, str):
        raise TypeError("speech text must be text")
    clauses: list[str] = []
    position = 0
    while position < len(text):
        length = _next_clause_length(text[position:])
        if length is None:
            break
        clauses.append(text[position:position + length])
        position += length
    if final and position < len(text):
        clauses.append(text[position:])
    return tuple(clauses)


def _next_clause_length(text: str) -> int | None:
    for index, character in enumerate(text, 1):
        if character in _CLAUSE_END:
            return index
        if index >= _SOFT_LENGTH and character in _SOFT_END:
            return index
        if index >= _HARD_LENGTH:
            return _HARD_LENGTH
    return None


def _locate_text_string(raw: str) -> tuple[int, dict[str, str]] | None:
    """Find a top-level text string after any complete preceding JSON members."""

    index = _skip_space(raw, 0)
    if index >= len(raw):
        return None
    if raw[index] != "{":
        raise ValueError("tool arguments must start with an object")
    index += 1
    metadata: dict[str, str] = {}
    decoder = json.JSONDecoder()
    while True:
        index = _skip_space(raw, index)
        if index >= len(raw):
            return None
        if raw[index] == "}":
            return None
        if raw[index] != '"':
            raise ValueError("object key must be a string")
        try:
            key, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            return None
        if not isinstance(key, str):
            raise ValueError("object key must be text")
        index = _skip_space(raw, end)
        if index >= len(raw):
            return None
        if raw[index] != ":":
            raise ValueError("object key must be followed by a colon")
        index = _skip_space(raw, index + 1)
        if index >= len(raw):
            return None
        if key == "text":
            if raw[index] != '"':
                raise ValueError("text must be a string")
            return index + 1, metadata
        try:
            value, end = decoder.raw_decode(raw, index)
        except json.JSONDecodeError:
            return None
        if key in {"motion_token", "expression_token"} and isinstance(value, str):
            metadata[key] = value
        index = _skip_space(raw, end)
        if index >= len(raw):
            return None
        if raw[index] == ",":
            index += 1
            continue
        if raw[index] == "}":
            return None
        raise ValueError("object member must end with comma or brace")


def _skip_space(raw: str, index: int) -> int:
    while index < len(raw) and raw[index] in " \t\r\n":
        index += 1
    return index


class _DuplicateKeyError(ValueError):
    pass


def _load_json_object(raw: str) -> Any:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateKeyError(key)
            result[key] = value
        return result

    return json.loads(
        raw, object_pairs_hook=unique,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )


def _contains_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, Mapping):
        return any(_contains_surrogate(key) or _contains_surrogate(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_surrogate(item) for item in value)
    return False


__all__ = ["SayClause", "SayTextStream", "ToolCallAssembler", "segment_say_text"]
