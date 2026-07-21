import asyncio
import importlib
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mcp.types import CallToolResult

from backend.agent.integration.mcp import (
    MCPClient,
    MCPConnectionError,
    MCPInitializationError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)


mcp_session_module = importlib.import_module("backend.agent.integration.mcp.session")


class FakeTransport:
    def __init__(self, *, enter_error: Exception | None = None):
        self.enter_error = enter_error
        self.closed = False

    async def __aenter__(self):
        if self.enter_error is not None:
            raise self.enter_error
        return "read-stream", "write-stream", lambda: None

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True


class FakeSession:
    def __init__(
        self,
        *,
        pages: list[object] | None = None,
        call_result: object | None = None,
        initialize_error: Exception | None = None,
        list_error: Exception | None = None,
        call_error: Exception | None = None,
        call_delay: float = 0,
    ):
        self.pages = list(pages or [])
        self.call_result = call_result
        self.initialize_error = initialize_error
        self.list_error = list_error
        self.call_error = call_error
        self.call_delay = call_delay
        self.initialized = False
        self.closed = False
        self.list_cursors: list[str | None] = []
        self.call_requests: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True

    async def initialize(self):
        if self.initialize_error is not None:
            raise self.initialize_error
        self.initialized = True

    async def list_tools(self, cursor=None):
        self.list_cursors.append(cursor)
        if self.list_error is not None:
            raise self.list_error
        return self.pages.pop(0)

    async def call_tool(self, name, arguments=None):
        self.call_requests.append((name, arguments))
        if self.call_delay:
            await asyncio.sleep(self.call_delay)
        if self.call_error is not None:
            raise self.call_error
        return self.call_result


@contextmanager
def patched_sdk(
    sessions: FakeSession | list[FakeSession],
    *,
    transports: FakeTransport | list[FakeTransport] | None = None,
):
    session_items = sessions if isinstance(sessions, list) else [sessions]
    transport_items = transports or [FakeTransport() for _ in session_items]
    if not isinstance(transport_items, list):
        transport_items = [transport_items]

    session_factory = Mock(side_effect=session_items)
    transport_factory = Mock(side_effect=transport_items)

    with (
        patch.object(mcp_session_module, "ClientSession", session_factory),
        patch.object(
            mcp_session_module,
            "streamable_http_client",
            transport_factory,
        ),
    ):
        yield session_factory, transport_factory


class MCPClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_call_tool_initializes_forwards_result_and_closes(self):
        result = CallToolResult(content=[], isError=True)
        session = FakeSession(call_result=result)
        transport = FakeTransport()
        client = MCPClient(timeout_seconds=1)

        with patched_sdk(session, transports=transport) as (
            session_factory,
            transport_factory,
        ):
            actual = await client.call_tool(
                "https://mcp.example.com/mcp",
                "search",
                {"query": "AuraBot"},
            )

        self.assertIs(actual, result)
        self.assertTrue(session.initialized)
        self.assertEqual(
            session.call_requests,
            [("search", {"query": "AuraBot"})],
        )
        self.assertTrue(session.closed)
        self.assertTrue(transport.closed)
        session_factory.assert_called_once_with("read-stream", "write-stream")
        transport_factory.assert_called_once_with("https://mcp.example.com/mcp")

    async def test_list_tools_reads_all_pages_in_one_session(self):
        first_tool = object()
        second_tool = object()
        session = FakeSession(
            pages=[
                SimpleNamespace(tools=[first_tool], nextCursor="page-2"),
                SimpleNamespace(tools=[second_tool], nextCursor=None),
            ]
        )
        client = MCPClient(timeout_seconds=1)

        with patched_sdk(session):
            tools = await client.list_tools("http://127.0.0.1:6402/mcp")

        self.assertEqual(tools, [first_tool, second_tool])
        self.assertEqual(session.list_cursors, [None, "page-2"])
        self.assertTrue(session.initialized)
        self.assertTrue(session.closed)

    async def test_each_operation_uses_a_fresh_session(self):
        first_session = FakeSession(
            call_result=CallToolResult(content=[], isError=False)
        )
        second_session = FakeSession(
            call_result=CallToolResult(content=[], isError=False)
        )
        client = MCPClient(timeout_seconds=1)

        with patched_sdk([first_session, second_session]) as (
            session_factory,
            _,
        ):
            await client.call_tool("https://mcp.example.com/mcp", "first", {})
            await client.call_tool("https://mcp.example.com/mcp", "second", {})

        self.assertEqual(session_factory.call_count, 2)
        self.assertTrue(first_session.closed)
        self.assertTrue(second_session.closed)

    async def test_rejects_invalid_inputs_before_opening_transport(self):
        session = FakeSession()
        client = MCPClient()

        with patched_sdk(session) as (_, transport_factory):
            invalid_calls = [
                ("localhost:6402/mcp", "search", {}, ValueError),
                ("ftp://example.com/mcp", "search", {}, ValueError),
                ("https://user@example.com/mcp", "search", {}, ValueError),
                ("https://example.com/mcp#fragment", "search", {}, ValueError),
                ("https://example.com/mcp", " search", {}, ValueError),
                ("https://example.com/mcp", "search", [], TypeError),
            ]

            for server_url, tool_name, arguments, error_type in invalid_calls:
                with self.subTest(server_url=server_url, tool_name=tool_name):
                    with self.assertRaises(error_type):
                        await client.call_tool(
                            server_url,
                            tool_name,
                            arguments,
                        )

        transport_factory.assert_not_called()

    async def test_maps_connection_and_initialization_failures(self):
        client = MCPClient(timeout_seconds=1)
        broken_transport = FakeTransport(enter_error=OSError("offline"))

        with patched_sdk(FakeSession(), transports=broken_transport):
            with self.assertRaises(MCPConnectionError):
                await client.list_tools("https://mcp.example.com/mcp")

        broken_session = FakeSession(
            initialize_error=RuntimeError("bad handshake")
        )
        transport = FakeTransport()
        with patched_sdk(broken_session, transports=transport):
            with self.assertRaises(MCPInitializationError):
                await client.list_tools("https://mcp.example.com/mcp")

        self.assertTrue(broken_session.closed)
        self.assertTrue(transport.closed)

    async def test_maps_list_errors_and_repeated_cursors(self):
        client = MCPClient(timeout_seconds=1)
        broken_session = FakeSession(list_error=RuntimeError("bad response"))

        with patched_sdk(broken_session):
            with self.assertRaises(MCPProtocolError):
                await client.list_tools("https://mcp.example.com/mcp")

        repeated_cursor_session = FakeSession(
            pages=[
                SimpleNamespace(tools=[], nextCursor="same"),
                SimpleNamespace(tools=[], nextCursor="same"),
            ]
        )
        with patched_sdk(repeated_cursor_session):
            with self.assertRaisesRegex(MCPProtocolError, "repeated"):
                await client.list_tools("https://mcp.example.com/mcp")

    async def test_maps_tool_call_failure_without_retry(self):
        session = FakeSession(call_error=RuntimeError("tool unavailable"))
        client = MCPClient(timeout_seconds=1)

        with patched_sdk(session) as (session_factory, _):
            with self.assertRaises(MCPToolCallError):
                await client.call_tool(
                    "https://mcp.example.com/mcp",
                    "write_data",
                    {"value": 1},
                )

        self.assertEqual(session_factory.call_count, 1)
        self.assertEqual(len(session.call_requests), 1)

    async def test_applies_one_timeout_to_full_operation(self):
        session = FakeSession(
            call_result=CallToolResult(content=[], isError=False),
            call_delay=0.05,
        )
        client = MCPClient(timeout_seconds=0.001)

        with patched_sdk(session):
            with self.assertRaises(MCPTimeoutError):
                await client.call_tool(
                    "https://mcp.example.com/mcp",
                    "slow_tool",
                    {},
                )

        self.assertTrue(session.closed)

    def test_rejects_non_positive_timeout(self):
        for timeout in (0, -1):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    MCPClient(timeout_seconds=timeout)


if __name__ == "__main__":
    unittest.main()
