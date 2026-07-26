import json
import unittest

from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.core.openai_chat_protocol import (
    OpenAIChatToolProtocol,
)
from backend.agent.tools.core.model import (
    FinalResponse,
    ToolCallBatch,
    ToolExecutionResult,
)
from backend.agent.tools.core.errors import (
    DuplicateToolNameError,
    ToolCallParseError,
)


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


def native_call(
    call_id: str,
    name: str,
    arguments: str = "{}",
) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


class OpenAIChatToolProtocolTests(unittest.TestCase):
    def test_encodes_tool_groups_as_openai_function_tools(self):
        schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        }
        groups = [
            ToolGroup(
                "search",
                [
                    Tool(
                        "web_search",
                        "Search the web",
                        "http://localhost:6400/mcp",
                        input_schema=schema,
                    )
                ],
                "Search tools",
            )
        ]

        definitions = OpenAIChatToolProtocol.encode_tools(groups)

        self.assertEqual(
            definitions,
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web",
                        "parameters": schema,
                    },
                }
            ],
        )
        self.assertIsNot(definitions[0]["function"]["parameters"], schema)

    def test_encode_tools_allows_no_authorized_tools(self):
        self.assertEqual(OpenAIChatToolProtocol.encode_tools([]), [])

    def test_encode_tools_rejects_duplicate_names(self):
        first = Tool(
            "search",
            "First",
            "http://localhost:6400/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        second = Tool(
            "search",
            "Second",
            "http://localhost:6401/mcp",
            input_schema=EMPTY_INPUT_SCHEMA,
        )

        with self.assertRaises(DuplicateToolNameError):
            OpenAIChatToolProtocol.encode_tools(
                [
                    ToolGroup("first", [first], "First group"),
                    ToolGroup("second", [second], "Second group"),
                ]
            )

    def test_parses_stop_as_final_response(self):
        response = OpenAIChatToolProtocol.parse_completion(
            {"role": "assistant", "content": "  Complete.  "},
            "stop",
        )

        self.assertEqual(response, FinalResponse("  Complete.  "))

    def test_parses_refusal_as_final_response(self):
        response = OpenAIChatToolProtocol.parse_completion(
            {
                "role": "assistant",
                "content": None,
                "refusal": "I cannot do that.",
            },
            "stop",
        )

        self.assertEqual(response, FinalResponse("I cannot do that."))

    def test_parses_single_native_tool_call_and_preserves_provider_id(self):
        response = OpenAIChatToolProtocol.parse_completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    native_call(
                        "call_provider_1",
                        "read_file",
                        '{"path":"a.txt"}',
                    )
                ],
            },
            "tool_calls",
        )

        self.assertIsInstance(response, ToolCallBatch)
        self.assertEqual(response.calls[0].call_id, "call_provider_1")
        self.assertEqual(response.calls[0].name, "read_file")
        self.assertEqual(response.calls[0].arguments, {"path": "a.txt"})

    def test_parses_multiple_calls_in_provider_order(self):
        response = OpenAIChatToolProtocol.parse_completion(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    native_call("provider_a", "first"),
                    native_call("provider_b", "second", '{"value":2}'),
                ],
            },
            "tool_calls",
        )

        self.assertIsInstance(response, ToolCallBatch)
        self.assertEqual(
            [(call.call_id, call.name) for call in response.calls],
            [("provider_a", "first"), ("provider_b", "second")],
        )

    def test_accepts_non_negative_provider_index_extension(self):
        indexed_call = native_call(
            "call_provider_1",
            "read_file",
            '{"path":"a.txt"}',
        )
        indexed_call["index"] = 0

        response = OpenAIChatToolProtocol.parse_completion(
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "I need to inspect the file.",
                "tool_calls": [indexed_call],
            },
            "tool_calls",
        )

        self.assertIsInstance(response, ToolCallBatch)
        self.assertEqual(response.calls[0].call_id, "call_provider_1")

    def test_rejects_finish_reason_message_mismatches(self):
        cases = [
            (
                {"role": "assistant", "content": "done", "tool_calls": []},
                "tool_calls",
                "invalid_tool_calls",
            ),
            (
                {
                    "role": "assistant",
                    "content": "done",
                    "tool_calls": [native_call("call_1", "search")],
                },
                "stop",
                "invalid_message",
            ),
            (
                {"role": "user", "content": "done"},
                "stop",
                "invalid_message",
            ),
        ]

        for message, finish_reason, code in cases:
            with self.subTest(finish_reason=finish_reason, message=message):
                with self.assertRaises(ToolCallParseError) as raised:
                    OpenAIChatToolProtocol.parse_completion(
                        message,
                        finish_reason,
                    )
                self.assertEqual(raised.exception.code, code)

    def test_maps_non_success_finish_reasons_to_stable_errors(self):
        cases = [
            ("length", "incomplete_completion"),
            ("content_filter", "content_filtered"),
            ("function_call", "invalid_finish_reason"),
            ("unknown", "invalid_finish_reason"),
        ]

        for finish_reason, code in cases:
            with self.subTest(finish_reason=finish_reason):
                with self.assertRaises(ToolCallParseError) as raised:
                    OpenAIChatToolProtocol.parse_completion(
                        {"role": "assistant", "content": None},
                        finish_reason,
                    )
                self.assertEqual(raised.exception.code, code)

    def test_rejects_invalid_call_shape_and_arguments(self):
        invalid_calls = [
            ({"type": "function", "function": {}}, "invalid_call"),
            (native_call("", "search"), "invalid_call"),
            (
                {
                    "id": "call_1",
                    "type": "custom",
                    "function": {"name": "search", "arguments": "{}"},
                },
                "invalid_call",
            ),
            (native_call("call_1", " search"), "invalid_call"),
            (native_call("call_1", "bad name"), "invalid_call"),
            (native_call("call_1", "search", "{"), "invalid_arguments"),
            (native_call("call_1", "search", "[]"), "invalid_arguments"),
            (
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": {}},
                },
                "invalid_arguments",
            ),
            (
                {
                    **native_call("call_1", "search"),
                    "index": -1,
                },
                "invalid_call",
            ),
            (
                {
                    **native_call("call_1", "search"),
                    "provider_extension": True,
                },
                "invalid_call",
            ),
        ]

        for raw_call, code in invalid_calls:
            with self.subTest(raw_call=raw_call):
                with self.assertRaises(ToolCallParseError) as raised:
                    OpenAIChatToolProtocol.parse_completion(
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [raw_call],
                        },
                        "tool_calls",
                    )
                self.assertEqual(raised.exception.code, code)

    def test_rejects_duplicate_call_ids_atomically(self):
        with self.assertRaises(ToolCallParseError) as raised:
            OpenAIChatToolProtocol.parse_completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        native_call("same", "first"),
                        native_call("same", "second"),
                    ],
                },
                "tool_calls",
            )

        self.assertEqual(raised.exception.code, "invalid_tool_calls")

    def test_encodes_one_ordered_tool_message_per_result(self):
        results = [
            ToolExecutionResult(
                tool_name="search",
                call_id="provider_a",
                content=[{"type": "text", "text": "found"}],
                structured_content={"count": 1},
                is_error=False,
            ),
            ToolExecutionResult(
                tool_name="read_file",
                call_id="provider_b",
                content=[{"type": "text", "text": "failed"}],
                structured_content={"error": {"code": "tool_error"}},
                is_error=True,
                error_code="tool_error",
            ),
        ]

        messages = OpenAIChatToolProtocol.encode_tool_messages(results)

        self.assertEqual(
            [message["tool_call_id"] for message in messages],
            ["provider_a", "provider_b"],
        )
        self.assertEqual(
            [message["role"] for message in messages],
            ["tool", "tool"],
        )
        self.assertEqual(
            json.loads(messages[0]["content"]),
            {
                "name": "search",
                "is_error": False,
                "content": [{"type": "text", "text": "found"}],
                "structured_content": {"count": 1},
                "error_code": None,
            },
        )

    def test_rejects_empty_or_duplicate_tool_message_ids(self):
        empty = ToolExecutionResult(
            tool_name="search",
            call_id="",
            content=[],
            structured_content=None,
            is_error=False,
        )
        duplicate = ToolExecutionResult(
            tool_name="search",
            call_id="same",
            content=[],
            structured_content=None,
            is_error=False,
        )

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            OpenAIChatToolProtocol.encode_tool_messages([])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            OpenAIChatToolProtocol.encode_tool_messages([empty])
        with self.assertRaisesRegex(ValueError, "must be unique"):
            OpenAIChatToolProtocol.encode_tool_messages([duplicate, duplicate])


if __name__ == "__main__":
    unittest.main()
