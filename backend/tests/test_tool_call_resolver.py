import unittest

from backend.agent.tools.Tool import Tool
from backend.agent.tools.core.ToolCallEntity import ToolCall
from backend.agent.tools.core.ToolCallResolver import ToolCallResolver
from backend.agent.tools.core.ToolExceptions import (
    ToolCallParseError,
    ToolNotAuthorizedError,
)


def make_tool(name: str, server_url: str = "http://mcp.test") -> Tool:
    return Tool(
        name=name,
        description=f"{name} tool",
        server_url=server_url,
        input_schema={"type": "object"},
    )


class ToolCallResolverTests(unittest.TestCase):
    def test_resolves_multiple_calls_and_preserves_order_and_call_id(self):
        read = make_tool("read")
        search = make_tool("search")
        resolver = ToolCallResolver({"read": read, "search": search})

        resolved = resolver.resolve(
            [
                ToolCall("search", {"query": "AuraBot"}, "call_1"),
                ToolCall("read", {"path": "TODO.md"}, "call_2"),
            ]
        )

        self.assertIs(resolved[0].tool, search)
        self.assertEqual(resolved[0].arguments, {"query": "AuraBot"})
        self.assertEqual(resolved[0].call_id, "call_1")
        self.assertIs(resolved[1].tool, read)

    def test_rejects_tool_outside_authorization_snapshot(self):
        resolver = ToolCallResolver({"read": make_tool("read")})

        with self.assertRaisesRegex(ToolNotAuthorizedError, "delete"):
            resolver.resolve([ToolCall("delete", {"path": "TODO.md"})])

    def test_empty_tool_call_batch_returns_empty_list(self):
        self.assertEqual(ToolCallResolver({}).resolve([]), [])

    def test_rejects_entire_batch_when_one_tool_is_not_authorized(self):
        resolver = ToolCallResolver({"read": make_tool("read")})

        with self.assertRaisesRegex(ToolNotAuthorizedError, "delete"):
            resolver.resolve(
                [
                    ToolCall("read", {"path": "TODO.md"}),
                    ToolCall("delete", {"path": "TODO.md"}),
                ]
            )

    def test_resolver_parses_and_resolves_llm_message(self):
        ping = make_tool("ping")
        resolver = ToolCallResolver({"ping": ping})
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "ping", "arguments": "{}"},
                }
            ],
        }

        resolved = resolver.resolve_message(message)

        self.assertEqual(len(resolved), 1)
        self.assertIs(resolved[0].tool, ping)
        self.assertEqual(resolved[0].call_id, "call_1")

    def test_resolve_message_does_not_wrap_parse_errors(self):
        resolver = ToolCallResolver({})

        with self.assertRaises(ToolCallParseError):
            resolver.resolve_message('{"tool":')


if __name__ == "__main__":
    unittest.main()
