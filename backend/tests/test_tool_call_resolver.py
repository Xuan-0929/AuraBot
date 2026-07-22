import unittest

from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import ToolCall
from backend.agent.tools.core.ToolCallResolver import ToolCallResolver
from backend.agent.tools.core.ToolExceptions import (
    DuplicateToolNameError,
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
            resolver.resolve(
                [ToolCall("delete", {"path": "TODO.md"}, "call_1")]
            )

    def test_empty_tool_call_batch_returns_empty_list(self):
        self.assertEqual(ToolCallResolver({}).resolve([]), [])

    def test_rejects_entire_batch_when_one_tool_is_not_authorized(self):
        resolver = ToolCallResolver({"read": make_tool("read")})

        with self.assertRaisesRegex(ToolNotAuthorizedError, "delete"):
            resolver.resolve(
                [
                    ToolCall("read", {"path": "TODO.md"}, "call_1"),
                    ToolCall("delete", {"path": "TODO.md"}, "call_2"),
                ]
            )

    def test_builds_authorization_snapshot_from_tool_groups(self):
        ping = make_tool("ping")
        status = make_tool("status")

        resolver = ToolCallResolver.from_tool_groups(
            [
                ToolGroup("first", [ping], "First tools"),
                ToolGroup("second", [status], "Second tools"),
            ]
        )

        resolved = resolver.resolve(
            [
                ToolCall("status", {}, "call_1"),
                ToolCall("ping", {}, "call_2"),
            ]
        )
        self.assertIs(resolved[0].tool, status)
        self.assertIs(resolved[1].tool, ping)

    def test_group_snapshot_rejects_duplicate_names(self):
        with self.assertRaisesRegex(DuplicateToolNameError, "ping"):
            ToolCallResolver.from_tool_groups(
                [
                    ToolGroup("first", [make_tool("ping")], "First tools"),
                    ToolGroup(
                        "second",
                        [make_tool("ping", "http://other.test")],
                        "Second tools",
                    ),
                ]
            )


if __name__ == "__main__":
    unittest.main()
