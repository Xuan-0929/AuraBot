"""
负责以无状态 Streamable HTTP 请求调用一个 MCP server。

ToolCallGateway 传入 server URL、工具名、参数和可信 metadata，本客户端完成握手与调用
并返回原始 MCP 结果；它不做 Task 授权、adaptor 注入或领域错误转换。
"""

import asyncio
from typing import Any

from mcp.types import CallToolResult, Tool as MCPTool

from .exceptions import MCPTimeoutError, MCPToolCallError
from .session import list_all_tools, open_session
from .validators import validate_server_url, validate_tool_call


class MCPClient:
    """Use one temporary ClientSession per operation.

    This object is safe to share through dependency injection. It does not
    cache sessions, own authentication state, retry calls, or require an async
    constructor.
    """

    def __init__(self, *, timeout_seconds: float | None = 30.0):
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive or None")
        self._timeout_seconds = timeout_seconds

    @property
    def timeout_seconds(self) -> float | None:
        return self._timeout_seconds

    async def list_tools(self, server_url: str) -> list[MCPTool]:
        """Initialize one session and read every tools/list page."""
        validate_server_url(server_url)

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with open_session(server_url) as session:
                    return await list_all_tools(session)
        except TimeoutError as exc:
            raise MCPTimeoutError("MCP tools/list timed out") from exc

    async def call_tool(
        self,
        server_url: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        """Initialize one session and forward one tools/call request."""
        validate_server_url(server_url)
        validate_tool_call(tool_name, arguments, meta)

        try:
            async with asyncio.timeout(self._timeout_seconds):
                async with open_session(server_url) as session:
                    try:
                        return await session.call_tool(
                            tool_name,
                            arguments=arguments,
                            meta=meta,
                        )
                    except Exception as exc:
                        raise MCPToolCallError("MCP tools/call failed") from exc
        except TimeoutError as exc:
            raise MCPTimeoutError("MCP tools/call timed out") from exc
