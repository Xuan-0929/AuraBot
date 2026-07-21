"""Temporary MCP ClientSession lifecycle and pagination helpers."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Tool as MCPTool

from .exceptions import (
    MCPConnectionError,
    MCPInitializationError,
    MCPProtocolError,
)


@asynccontextmanager
async def open_session(server_url: str) -> AsyncIterator[ClientSession]:
    async with AsyncExitStack() as stack:
        try:
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(server_url)
            )
            session = await stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
        except Exception as exc:
            raise MCPConnectionError("Failed to open MCP connection") from exc

        try:
            await session.initialize()
        except Exception as exc:
            raise MCPInitializationError(
                "Failed to initialize MCP session"
            ) from exc

        yield session


async def list_all_tools(session: ClientSession) -> list[MCPTool]:
    tools: list[MCPTool] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()

    while True:
        try:
            page = await session.list_tools(cursor=cursor)
        except Exception as exc:
            raise MCPProtocolError("MCP tools/list failed") from exc

        tools.extend(page.tools)
        next_cursor = page.nextCursor
        if next_cursor is None:
            return tools
        if not isinstance(next_cursor, str) or not next_cursor:
            raise MCPProtocolError(
                "MCP tools/list returned an invalid pagination cursor"
            )
        if next_cursor in seen_cursors:
            raise MCPProtocolError(
                "MCP tools/list returned a repeated pagination cursor"
            )

        seen_cursors.add(next_cursor)
        cursor = next_cursor
