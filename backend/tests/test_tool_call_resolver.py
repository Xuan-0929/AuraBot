import unittest

from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import ToolCall
from backend.agent.tools.core.ToolCallResolver import ToolCallResolver
from backend.agent.tools.core.ToolExceptions import (
    ToolCallParseError,
    ToolCallResolveError,
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
        resolver = ToolCallResolver(
            [ToolGroup("files", [read, search], "File tools")]
        )

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

    def test_rejects_unknown_or_unauthorized_tool(self):
        resolver = ToolCallResolver(
            [ToolGroup("files", [make_tool("read")], "File tools")]
        )

        with self.assertRaisesRegex(ToolCallResolveError, "delete"):
            resolver.resolve([ToolCall("delete", {"path": "TODO.md"})])

    def test_rejects_ambiguous_authorized_tool_names(self):
        groups = [
            ToolGroup(
                "local-search",
                [make_tool("search", "http://local.test")],
                "Local search",
            ),
            ToolGroup(
                "remote-search",
                [make_tool("search", "http://remote.test")],
                "Remote search",
            ),
        ]

        with self.assertRaisesRegex(ToolCallResolveError, "ambiguous"):
            ToolCallResolver(groups)

    def test_resolver_parses_and_resolves_llm_message(self):
        ping = make_tool("ping")
        resolver = ToolCallResolver(
            [ToolGroup("system", [ping], "System tools")]
        )
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
        resolver = ToolCallResolver(
            [ToolGroup("system", [], "System tools")]
        )

        with self.assertRaises(ToolCallParseError):
            resolver.resolve_message('{"tool":')


if __name__ == "__main__":
    unittest.main()
