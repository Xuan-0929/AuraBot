"""
验证 ToolCallGateway 的 MCP 调用、内容归一化与错误结果转换。

测试在 transport 边界使用受控 MCP client 和真实 ResolvedToolCall，不覆盖 resolver 授权。
"""

import tempfile
import unittest
from pathlib import Path

from mcp.types import CallToolResult, ImageContent, TextContent

from backend.agent.integration.mcp import (
    MCPClientError,
    MCPConnectionError,
    MCPInitializationError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPToolCallError,
)
from backend.agent.task.harness.adaptors.file_system import (
    FileSystemAdaptor,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.core.model import (
    ResolvedToolCall,
    ToolExecutionResult,
)
from backend.agent.tools.core.gateway import ToolCallGateway


class FakeMCPClient:
    def __init__(self, outcomes: list[object] | None = None):
        self.outcomes = list(outcomes or [])
        self.requests: list[tuple[str, str, dict, dict | None]] = []

    async def call_tool(
        self,
        server_url,
        tool_name,
        arguments,
        *,
        meta=None,
    ):
        self.requests.append((server_url, tool_name, arguments, meta))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_call(
    name: str = "search",
    arguments: dict | None = None,
    *,
    call_id: str = "call_1",
    server_url: str = "https://mcp.example.com/mcp",
    input_schema: dict | None = None,
) -> ResolvedToolCall:
    tool = Tool(
        name=name,
        description=f"{name} tool",
        server_url=server_url,
        input_schema=input_schema
        or {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    )
    return ResolvedToolCall(
        tool=tool,
        arguments=arguments if arguments is not None else {"query": "AuraBot"},
        call_id=call_id,
    )


class ToolCallGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_filesystem_adapted_bash_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory).resolve()
            tool = Tool(
                "bash",
                "Run Bash",
                "https://mcp.example.com/mcp",
                input_schema={
                    "type": "object",
                    "properties": {
                        "statement": {
                            "type": "string",
                            "minLength": 1,
                        },
                        "PATH": {
                            "type": "string",
                            "minLength": 1,
                        },
                    },
                    "required": ["statement", "PATH"],
                    "additionalProperties": False,
                },
                adaptor_names=("FileSystemAdaptor",),
            )
            call = ResolvedToolCall(
                tool=tool,
                arguments={"statement": "pwd", "PATH": "."},
                call_id="call_bash",
            )
            adapted = FileSystemAdaptor(str(workdir)).adapt(call)
            client = FakeMCPClient(
                [
                    CallToolResult(
                        content=[TextContent(type="text", text=str(workdir))],
                        structuredContent={
                            "cwd": str(workdir),
                            "stdout": str(workdir),
                        },
                        isError=False,
                    )
                ]
            )
            gateway = ToolCallGateway(client)  # type: ignore[arg-type]

            result = await gateway.execute(adapted)

        self.assertFalse(result.is_error)
        self.assertEqual(
            client.requests[0][2],
            {"statement": "pwd", "PATH": str(workdir)},
        )

    async def test_executes_and_normalizes_mcp_result(self):
        mcp_result = CallToolResult(
            content=[
                TextContent(type="text", text="found"),
                ImageContent(
                    type="image",
                    data="aW1hZ2U=",
                    mimeType="image/png",
                ),
            ],
            structuredContent={"count": 1},
            isError=False,
        )
        client = FakeMCPClient([mcp_result])
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        result = await gateway.execute(make_call())

        self.assertIsInstance(result, ToolExecutionResult)
        self.assertEqual(result.tool_name, "search")
        self.assertEqual(result.call_id, "call_1")
        self.assertEqual(
            result.content,
            [
                {"type": "text", "text": "found"},
                {
                    "type": "image",
                    "data": "aW1hZ2U=",
                    "mimeType": "image/png",
                },
            ],
        )
        self.assertEqual(result.structured_content, {"count": 1})
        self.assertFalse(result.is_error)
        self.assertIsNone(result.error_code)
        self.assertEqual(
            client.requests,
            [
                (
                    "https://mcp.example.com/mcp",
                    "search",
                    {"query": "AuraBot"},
                    None,
                )
            ],
        )

    async def test_forwards_mcp_metadata_separately_from_arguments(self):
        client = FakeMCPClient(
            [CallToolResult(content=[], structuredContent={}, isError=False)]
        )
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]
        call = make_call()
        call = ResolvedToolCall(
            tool=call.tool,
            arguments=call.arguments,
            call_id=call.call_id,
            mcp_meta={"aurabot": {"task_name": "Task"}},
        )

        await gateway.execute(call)

        self.assertEqual(
            client.requests,
            [
                (
                    "https://mcp.example.com/mcp",
                    "search",
                    {"query": "AuraBot"},
                    {"aurabot": {"task_name": "Task"}},
                )
            ],
        )

    async def test_normalizes_mcp_tool_error(self):
        client = FakeMCPClient(
            [
                CallToolResult(
                    content=[TextContent(type="text", text="not found")],
                    isError=True,
                )
            ]
        )
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        result = await gateway.execute(make_call())

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, "tool_error")
        self.assertEqual(result.content[0]["text"], "not found")

    async def test_invalid_arguments_do_not_call_mcp(self):
        client = FakeMCPClient()
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        result = await gateway.execute(make_call(arguments={}))

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, "invalid_arguments")
        self.assertEqual(client.requests, [])

    async def test_invalid_tool_schema_does_not_call_mcp(self):
        client = FakeMCPClient()
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]
        call = make_call(
            input_schema={"type": "not-a-json-schema-type"},
        )

        result = await gateway.execute(call)

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, "invalid_tool_schema")
        self.assertEqual(client.requests, [])

    async def test_maps_mcp_errors_without_leaking_original_message(self):
        cases = [
            (MCPConnectionError("secret connection detail"), "mcp_connection"),
            (
                MCPInitializationError("secret initialization detail"),
                "mcp_initialization",
            ),
            (MCPProtocolError("secret protocol detail"), "mcp_protocol"),
            (MCPTimeoutError("secret timeout detail"), "mcp_timeout"),
            (MCPToolCallError("secret tool detail"), "mcp_tool_call"),
            (MCPClientError("secret client detail"), "mcp_tool_call"),
        ]

        for error, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                client = FakeMCPClient([error])
                gateway = ToolCallGateway(client)  # type: ignore[arg-type]

                result = await gateway.execute(make_call())

                self.assertTrue(result.is_error)
                self.assertEqual(result.error_code, expected_code)
                self.assertNotIn("secret", str(result.content))
                self.assertNotIn("secret", str(result.structured_content))

    async def test_maps_invalid_mcp_route(self):
        client = FakeMCPClient([ValueError("secret invalid URL")])
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        result = await gateway.execute(make_call(server_url="localhost:6410"))

        self.assertTrue(result.is_error)
        self.assertEqual(result.error_code, "invalid_tool_route")
        self.assertNotIn("secret", str(result.content))

    async def test_execute_many_is_sequential_and_continues_after_tool_error(self):
        client = FakeMCPClient(
            [
                CallToolResult(
                    content=[TextContent(type="text", text="first failed")],
                    isError=True,
                ),
                CallToolResult(
                    content=[TextContent(type="text", text="second succeeded")],
                    isError=False,
                ),
            ]
        )
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]
        calls = [
            make_call("first", call_id="call_1"),
            make_call("second", call_id="call_2"),
        ]

        results = await gateway.execute_many(calls)

        self.assertEqual([result.tool_name for result in results], ["first", "second"])
        self.assertEqual([result.is_error for result in results], [True, False])
        self.assertEqual(
            [request[1] for request in client.requests],
            ["first", "second"],
        )

    async def test_execute_many_accepts_an_empty_batch(self):
        client = FakeMCPClient()
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        self.assertEqual(await gateway.execute_many([]), [])
        self.assertEqual(client.requests, [])

    async def test_unexpected_programming_error_is_not_converted(self):
        client = FakeMCPClient([RuntimeError("programming error")])
        gateway = ToolCallGateway(client)  # type: ignore[arg-type]

        with self.assertRaisesRegex(RuntimeError, "programming error"):
            await gateway.execute(make_call())


if __name__ == "__main__":
    unittest.main()
