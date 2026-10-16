"""Decode bounded SAY envelopes and assemble indexed OpenAI tool deltas without field-order assumptions.

The finite top-level scanner identifies only text/avatar. The incremental string decoder releases a
complete first sentence after four speech units, then groups complete sentences to at least 45 units;
English words count as two, other non-punctuation characters as one, and whitespace counts as zero.
There is no length-based sentence cut. Closing the text string flushes its tail immediately, even
before trailing avatar fields or other tools finish. Spaces and punctuation remain lossless. Strict final decoding rejects
duplicate keys and malformed envelopes, including
errors discovered after early delivery. Avatar stays optional and is frozen by the authorized Harness.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .definitions import ToolCall


_SENTENCE_END = frozenset("。！？!?.")
_SENTENCE_CLOSE = frozenset('”’」』）)]}»"\'')
_SPEECH_UNITS = re.compile(r"[A-Za-z]+|[^A-Za-z]", re.DOTALL)
FIRST_SENTENCE_UNITS = 4
FOLLOWING_SENTENCE_UNITS = 45
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class SayTextStream:
    """Incrementally decode top-level text, retaining only complete optional avatar data."""

    def __init__(self, max_length: int = 8_000) -> None:
        self.max_length = max_length
        self.raw = ""
        self.position: int | None = None
        self.decoded = ""
        self.emitted = 0
        self.closed = False
        self.error_code: str | None = None
        self.avatar: Any = None
        self.object_closed = False

    @property
    def remaining_text(self) -> str:
        return self.decoded[self.emitted:]

    @property
    def invalid(self) -> bool:
        return self.error_code is not None

    def feed(self, fragment: str) -> tuple[str, ...]:
        if not isinstance(fragment, str):
            raise TypeError("tool argument fragments must be strings")
        self.raw += fragment
        if self.invalid:
            return ()
        if len(self.raw) > 64 * 1024:
            self.error_code = "schema_invalid"
            return ()
        try:
            start, avatar, complete = _scan_say(self.raw)
            self.avatar = avatar
            self.object_closed = complete
            if self.position is None:
                self.position = start
        except _DuplicateKey:
            self.error_code = "duplicate_json_key"
            return ()
        except ValueError:
            self.error_code = "malformed_arguments"
            return ()
        if self.position is None:
            return ()
        while not self.closed and self.position < len(self.raw):
            character = self.raw[self.position]
            if character == '"':
                self.closed = True
                self.position += 1
                break
            if character == "\\":
                decoded = self._escape()
                if decoded is None:
                    break
                value, consumed = decoded
                self.position += consumed
            else:
                if ord(character) < 0x20:
                    self.error_code = "malformed_arguments"
                    break
                if 0xD800 <= ord(character) <= 0xDFFF:
                    self.error_code = "invalid_unicode_surrogate"
                    break
                value = character
                self.position += 1
            if len(self.decoded) + len(value) > self.max_length:
                self.error_code = "schema_invalid"
                break
            self.decoded += value
        if self.invalid:
            return ()
        clauses = segment_say_text(self.remaining_text, final=self.closed, first=self.emitted == 0)
        self.emitted += sum(map(len, clauses))
        return clauses

    def _escape(self) -> tuple[str, int] | None:
        assert self.position is not None
        if self.position + 1 >= len(self.raw):
            return None
        marker = self.raw[self.position + 1]
        if marker in _ESCAPES:
            return _ESCAPES[marker], 2
        if marker != "u":
            self.error_code = "malformed_arguments"
            return "", 2
        if self.position + 6 > len(self.raw):
            return None
        digits = self.raw[self.position + 2:self.position + 6]
        if not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
            self.error_code = "malformed_arguments"
            return "", 6
        high = int(digits, 16)
        if 0xD800 <= high <= 0xDBFF:
            if self.position + 12 > len(self.raw):
                return None
            suffix = self.raw[self.position + 6:self.position + 12]
            if re.fullmatch(r"\\u[0-9a-fA-F]{4}", suffix):
                low = int(suffix[2:], 16)
                if 0xDC00 <= low <= 0xDFFF:
                    return chr(0x10000 + ((high - 0xD800) << 10) + low - 0xDC00), 12
            self.error_code = "invalid_unicode_surrogate"
            return "", 6
        if 0xDC00 <= high <= 0xDFFF:
            self.error_code = "invalid_unicode_surrogate"
            return "", 6
        return chr(high), 6


@dataclass(frozen=True, slots=True)
class SayClause:
    index: int
    call_id: str
    text: str
    avatar: Any = None


@dataclass(slots=True)
class _PartialCall:
    index: int
    call_id: str = ""
    name: str = ""
    raw_arguments: str = ""
    say_stream: SayTextStream | None = None
    pending_clauses: list[str] = field(default_factory=list)
    parse_error: str | None = None


class ToolCallAssembler:
    """Track indexed Chat Completions function calls and retain malformed calls for receipts."""

    def __init__(self) -> None:
        self.calls: dict[int, _PartialCall] = {}
        self.ids: dict[str, int] = {}
        self.assistant_content_seen = False
        self.protocol_error: str | None = None

    def feed(self, chunk: Mapping[str, Any]) -> tuple[SayClause, ...]:
        if not isinstance(chunk, Mapping):
            raise TypeError("provider chunks must be mappings")
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            self.protocol_error = "provider_stream_error"
            return ()
        try:
            for choice in choices:
                if not isinstance(choice, Mapping) or not isinstance(choice.get("delta", {}), Mapping):
                    raise ValueError
                delta = choice.get("delta", {})
                self.assistant_content_seen |= delta.get("content") not in (None, "")
                items = delta.get("tool_calls", [])
                if not isinstance(items, list):
                    raise ValueError
                for item in items:
                    self._feed_call(item)
        except (TypeError, ValueError):
            self.protocol_error = "provider_stream_error"
            return ()
        clauses: list[SayClause] = []
        for index in sorted(self.calls):
            partial = self.calls[index]
            clauses.extend(SayClause(index, partial.call_id, text, partial.say_stream.avatar if partial.say_stream else None) for text in partial.pending_clauses)
        return tuple(clauses)

    def _feed_call(self, item: Any) -> None:
        if not isinstance(item, Mapping) or type(item.get("index")) is not int or item["index"] < 0:
            raise ValueError
        index = item["index"]
        partial = self.calls.setdefault(index, _PartialCall(index))
        call_id = item.get("id")
        if call_id is not None:
            if not isinstance(call_id, str) or not call_id or (partial.call_id and partial.call_id != call_id):
                partial.parse_error = "ambiguous_call_identity"
            elif call_id in self.ids and self.ids[call_id] != index:
                partial.parse_error = "ambiguous_call_identity"
            else:
                partial.call_id = call_id
                self.ids[call_id] = index
        function = item.get("function", {})
        if not isinstance(function, Mapping):
            raise ValueError
        name = function.get("name")
        if name is not None:
            if not isinstance(name, str) or not name or (partial.name and partial.name != name):
                partial.parse_error = "conflicting_tool_name"
            else:
                partial.name = name
        arguments = function.get("arguments", "")
        if not isinstance(arguments, str):
            raise ValueError
        if len(partial.raw_arguments) + len(arguments) > 64 * 1024:
            partial.parse_error = "arguments_too_large"
            partial.pending_clauses.clear()
            return
        if partial.parse_error == "arguments_too_large":
            return
        if partial.name == "say_to_user" and partial.say_stream is None:
            partial.say_stream = SayTextStream()
            if partial.raw_arguments:
                partial.pending_clauses.extend(partial.say_stream.feed(partial.raw_arguments))
        partial.raw_arguments += arguments
        if partial.say_stream is not None and arguments:
            partial.pending_clauses.extend(partial.say_stream.feed(arguments))
            if partial.say_stream.invalid:
                partial.pending_clauses.clear()

    def accept_say_clause(self, index: int, text: str) -> None:
        partial = self.calls[index]
        if not partial.pending_clauses or partial.pending_clauses[0] != text:
            raise ValueError("say clause acceptance is out of order")
        partial.pending_clauses.pop(0)

    def can_stream_say(self, index: int, max_calls: int) -> bool:
        partial = self.calls.get(index)
        return bool(
            partial
            and index < max_calls
            and partial.call_id
            and partial.name == "say_to_user"
            and not partial.parse_error
            and all(
                lower in self.calls
                and self.calls[lower].call_id
                and self.calls[lower].name
                and self.calls[lower].name != "say_to_user"
                and not self.calls[lower].parse_error
                for lower in range(index)
            )
        )

    def say_stream(self, index: int) -> SayTextStream | None:
        partial = self.calls.get(index)
        return partial.say_stream if partial else None

    def finish(self) -> tuple[ToolCall, ...]:
        result = []
        for index in sorted(self.calls):
            partial = self.calls[index]
            error = partial.parse_error
            arguments: Mapping[str, Any] = {}
            try:
                arguments = _load_object(partial.raw_arguments)
                if _contains_surrogate(arguments):
                    error = error or "invalid_unicode_surrogate"
            except _DuplicateKey:
                error = error or "duplicate_json_key"
            except (json.JSONDecodeError, TypeError, ValueError):
                error = error or "malformed_arguments"
            if partial.say_stream and partial.say_stream.invalid:
                error = error or partial.say_stream.error_code
            if not partial.call_id:
                error = error or "ambiguous_call_identity"
            if not partial.name:
                error = error or "conflicting_tool_name"
            if self.protocol_error:
                error = "provider_stream_error"
            result.append(ToolCall(
                partial.call_id or f"call_{index}", partial.name, arguments, index,
                partial.raw_arguments, error,
            ))
        return tuple(result)


def speech_units(text: str) -> int:
    """Count English letter runs as two; punctuation and spaces are separators, never units."""
    return sum(
        2 if token[0].isascii() and token[0].isalpha() else
        int(not token.isspace() and not unicodedata.category(token).startswith("P"))
        for token in _SPEECH_UNITS.findall(text)
    )


def clause_flush_reason(text: str, *, first: bool) -> str:
    """Explain the shared segmentation policy for content-free Harness diagnostics."""
    target = FIRST_SENTENCE_UNITS if first else FOLLOWING_SENTENCE_UNITS
    tail = text.rstrip().rstrip("".join(_SENTENCE_CLOSE))
    if tail and tail[-1] in _SENTENCE_END and speech_units(text) >= target:
        return "first_sentence" if first else "sentence_target"
    return "text_end"


def segment_say_text(text: str, *, final: bool, first: bool = True) -> tuple[str, ...]:
    clauses: list[str] = []
    position = 0
    while position < len(text):
        length = _next_clause_length(text[position:], first=first and not clauses, final=final)
        if length is None:
            break
        clauses.append(text[position:position + length])
        position += length
    if final and position < len(text):
        clauses.append(text[position:])
    return tuple(clauses)


def _next_clause_length(text: str, *, first: bool, final: bool) -> int | None:
    target = FIRST_SENTENCE_UNITS if first else FOLLOWING_SENTENCE_UNITS
    for index, character in enumerate(text, 1):
        if character not in _SENTENCE_END:
            continue
        # A dot within a version, decimal or domain is not a sentence boundary.
        if character == "." and index < len(text) and text[index].isascii() and (
            text[index].isalnum() or text[index] in "_/"
        ):
            continue
        end = index
        while end < len(text) and text[end] in _SENTENCE_END | _SENTENCE_CLOSE:
            end += 1
        # One-character lookahead retains closing quotes and punctuation clusters across chunks.
        if not text[end:].strip():
            if not final:
                return None
            end = len(text)  # Keep trailing whitespace with speech, never synthesize it alone.
        if speech_units(text[:end]) >= target:
            return end
    return None


def _scan_say(raw: str) -> tuple[int | None, Any, bool]:
    """Scan the two legal members using stdlib JSON tokens; incomplete input is never guessed."""
    decoder = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    position = 0
    text_start = None
    avatar = None
    seen: set[str] = set()

    def whitespace(index: int) -> int:
        while index < len(raw) and raw[index] in " \t\r\n":
            index += 1
        return index

    position = whitespace(position)
    if position == len(raw):
        return text_start, avatar, False
    if raw[position] != "{":
        raise ValueError("SAY envelope must be an object")
    position += 1
    after_comma = False
    while True:
        position = whitespace(position)
        if position == len(raw):
            return text_start, avatar, False
        if raw[position] == "}":
            if after_comma or "text" not in seen or whitespace(position + 1) != len(raw):
                raise ValueError("invalid SAY envelope")
            return text_start, avatar, True
        if raw[position] != '"':
            raise ValueError("invalid member")
        try:
            key, position = decoder.raw_decode(raw, position)
        except json.JSONDecodeError:
            return text_start, avatar, False
        if key in seen:
            raise _DuplicateKey(key)
        if key not in {"text", "avatar"}:
            raise ValueError("unknown SAY member")
        seen.add(key)
        position = whitespace(position)
        if position == len(raw):
            return text_start, avatar, False
        if raw[position] != ":":
            raise ValueError("invalid member separator")
        position = whitespace(position + 1)
        if position == len(raw):
            return text_start, avatar, False
        if key == "text":
            if raw[position] != '"':
                raise ValueError("text must be string")
            text_start = position + 1
        try:
            value, position = decoder.raw_decode(raw, position)
        except json.JSONDecodeError:
            return text_start, avatar, False
        if _contains_surrogate(value):
            raise ValueError("invalid Unicode")
        if key == "avatar":
            avatar = value
        position = whitespace(position)
        if position == len(raw):
            return text_start, avatar, False
        if raw[position] == ",":
            position += 1
            after_comma = True
        elif raw[position] == "}":
            after_comma = False
        else:
            raise ValueError("invalid member boundary")


class _DuplicateKey(ValueError):
    pass


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _load_object(raw: str) -> dict[str, Any]:
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(value, dict):
        raise ValueError("tool arguments must be an object")
    return value


def _contains_surrogate(value: Any) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, Mapping):
        return any(_contains_surrogate(key) or _contains_surrogate(item) for key, item in value.items())
    return isinstance(value, list) and any(_contains_surrogate(item) for item in value)


__all__ = ["SayClause", "SayTextStream", "ToolCallAssembler", "segment_say_text", "speech_units", "clause_flush_reason"]
