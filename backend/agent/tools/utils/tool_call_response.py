"""Low-level helpers for decoding LLM tool-call responses."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from ..core.ToolExceptions import ToolCallParseError


def decode_tool_call_response(response: Any) -> Any:
    """Decode supported response containers and JSON-formatted text."""

    if isinstance(response, Mapping):
        return response
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return response
    if not isinstance(response, str):
        raise TypeError("response must be a string, mapping, or message list")

    text = response.strip()
    if not text:
        return None

    json_text, had_code_fence = _unwrap_json_code_fence(text)
    try:
        return json.loads(json_text)
    except json.JSONDecodeError as exc:
        if had_code_fence or json_text.startswith(("{", "[")):
            raise ToolCallParseError("LLM tool-call JSON is invalid") from exc
        return None


def extract_raw_tool_calls(payload: Any) -> list[Any]:
    """Extract raw call objects from supported response shapes."""

    if payload is None or isinstance(payload, (str, int, float, bool)):
        return []

    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        items = list(payload)
        if _is_message_list(items):
            calls = []
            for item in items:
                calls.extend(extract_raw_tool_calls(item))
            return calls
        return items

    if not isinstance(payload, Mapping):
        return []

    payload = _unwrap_response_payload(payload)

    if "tool_calls" in payload:
        calls = payload["tool_calls"]
        if not isinstance(calls, Sequence) or isinstance(calls, (str, bytes)):
            raise ToolCallParseError("tool_calls must be a list")
        return list(calls)

    if "tool" in payload:
        return _extract_custom_tool_field(payload)

    if "function" in payload or (
        any(key in payload for key in ("name", "tool_name"))
        and "arguments" in payload
    ):
        return [payload]

    if isinstance(payload.get("content"), str):
        decoded_content = decode_tool_call_response(payload["content"])
        return extract_raw_tool_calls(decoded_content)

    return []


def parse_tool_arguments(arguments: Any, location: str) -> dict[str, Any]:
    """Decode arguments and require a JSON object."""

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolCallParseError(
                f"{location} arguments are invalid JSON"
            ) from exc

    if not isinstance(arguments, Mapping):
        raise ToolCallParseError(f"{location} arguments must be a JSON object")
    return dict(arguments)


def _unwrap_json_code_fence(text: str) -> tuple[str, bool]:
    if not text.startswith("```"):
        return text, False

    lines = text.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return text, True

    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return text, True
    return "\n".join(lines[1:-1]).strip(), True


def _is_message_list(items: list[Any]) -> bool:
    message_keys = ("role", "choices", "message", "tool_calls")
    return all(isinstance(item, Mapping) for item in items) and any(
        any(key in item for key in message_keys) for item in items
    )


def _unwrap_response_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if "choices" in payload:
        choices = payload["choices"]
        if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
            raise ToolCallParseError("choices must be a list")
        if not choices:
            return {}
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise ToolCallParseError("choices[0] must be an object")
        payload = first_choice.get("message", first_choice)
        if not isinstance(payload, Mapping):
            raise ToolCallParseError("choices[0].message must be an object")

    if "message" in payload and "tool_calls" not in payload:
        message = payload["message"]
        if not isinstance(message, Mapping):
            raise ToolCallParseError("message must be an object")
        return message
    return payload


def _extract_custom_tool_field(payload: Mapping[str, Any]) -> list[Any]:
    tool_value = payload["tool"]
    if isinstance(tool_value, Sequence) and not isinstance(
        tool_value, (str, bytes)
    ):
        return list(tool_value)
    if isinstance(tool_value, Mapping):
        return [tool_value]
    if isinstance(tool_value, str):
        return [payload]
    raise ToolCallParseError("tool must be a name, object, or list")


__all__ = [
    "decode_tool_call_response",
    "extract_raw_tool_calls",
    "parse_tool_arguments",
]
