"""Streamable HTTP MCP server for AuraBot native FileSystem tools."""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Literal

from jsonschema import ValidationError, validate
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent, Tool

from .errors import ToolFailure
from .schemas import TOOL_BY_NAME, TOOL_DEFINITIONS
from .tools import TOOL_HANDLERS


logger = logging.getLogger(__name__)


class NativeFileSystemMCP(FastMCP[None]):
    """FastMCP transport with explicit, document-defined Tool schemas."""

    async def list_tools(self) -> list[Tool]:
        return list(TOOL_DEFINITIONS)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> CallToolResult:
        tool = TOOL_BY_NAME.get(name)
        handler = TOOL_HANDLERS.get(name)
        if tool is None or handler is None:
            return _error_result("unknown_tool", "Requested tool is not available.")

        try:
            validate(instance=arguments, schema=tool.inputSchema)
        except ValidationError:
            return _error_result(
                "invalid_arguments",
                "Tool arguments do not match the declared input schema.",
            )

        try:
            value = handler(arguments)
            if inspect.isawaitable(value):
                value = await value
            validate(instance=value, schema=tool.outputSchema)
        except ToolFailure as exc:
            return _error_result(exc.code, exc.message)
        except ValidationError:
            logger.exception("Tool %s produced an invalid structured result", name)
            return _error_result(
                "invalid_result",
                "Tool produced an invalid structured result.",
            )
        except Exception:
            logger.exception("Unexpected FileSystem tool failure: %s", name)
            return _error_result("internal_error", "Tool execution failed.")

        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(value, ensure_ascii=False, sort_keys=True),
                )
            ],
            structuredContent=value,
            isError=False,
        )


def _error_result(code: str, message: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent={"error": {"code": code, "message": message}},
        isError=True,
    )


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 6410,
    streamable_http_path: str = "/mcp",
    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO",
) -> NativeFileSystemMCP:
    return NativeFileSystemMCP(
        name="AuraBot Native FileSystem",
        instructions="AuraBot Task Agent native filesystem tools.",
        host=host,
        port=port,
        streamable_http_path=streamable_http_path,
        json_response=True,
        stateless_http=True,
        log_level=log_level,
    )


__all__ = ["NativeFileSystemMCP", "create_server"]
