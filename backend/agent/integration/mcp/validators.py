"""Input validation at the MCP transport boundary."""

from typing import Any
from urllib.parse import urlsplit


def validate_server_url(server_url: str) -> None:
    if (
        not isinstance(server_url, str)
        or not server_url
        or server_url != server_url.strip()
        or any(character.isspace() for character in server_url)
    ):
        raise ValueError("server_url must be a non-empty absolute HTTP URL")

    try:
        parsed = urlsplit(server_url)
        parsed.port
    except ValueError as exc:
        raise ValueError("server_url must be a valid absolute HTTP URL") from exc

    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError("server_url must be a valid absolute HTTP URL")


def validate_tool_call(
    tool_name: str,
    arguments: dict[str, Any],
    meta: dict[str, Any] | None = None,
) -> None:
    if (
        not isinstance(tool_name, str)
        or not tool_name
        or tool_name != tool_name.strip()
    ):
        raise ValueError("tool_name must be a non-empty string")
    if not isinstance(arguments, dict):
        raise TypeError("arguments must be a dict")
    if meta is not None and not isinstance(meta, dict):
        raise TypeError("meta must be a dict or None")
