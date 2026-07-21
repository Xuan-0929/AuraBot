import unittest

from backend.agent.tools.core.ToolCallEntity import ToolCall
from backend.agent.tools.core.ToolExceptions import ToolCallParseError
from backend.agent.tools.core.parser import (
    parse_tool_call,
    parse_tool_calls,
)


class ToolCallParserTests(unittest.TestCase):
    def test_plain_final_answer_has_no_tool_calls(self):
        self.assertEqual(parse_tool_calls("The task is complete."), [])

    def test_parses_multiple_calls_from_content_tool_field(self):
        content = """
        {
          "tool": [
            {"name": "read_file", "arguments": {"path": "a.txt"}},
            {"name": "search", "arguments": {"query": "AuraBot"}}
          ]
        }
        """

        calls = parse_tool_calls(content)

        self.assertEqual(
            calls,
            [
                ToolCall("read_file", {"path": "a.txt"}),
                ToolCall("search", {"query": "AuraBot"}),
            ],
        )

    def test_parses_openai_compatible_response_and_argument_strings(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"a.txt"}',
                                },
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"query":"AuraBot"}',
                                },
                            },
                        ],
                    }
                }
            ]
        }

        calls = parse_tool_calls(response)

        self.assertEqual(
            calls,
            [
                ToolCall("read_file", {"path": "a.txt"}, "call_1"),
                ToolCall("search", {"query": "AuraBot"}, "call_2"),
            ],
        )

    def test_parses_llm_client_message(self):
        response = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path":"a.txt"}',
                    },
                },
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "search",
                        "arguments": '{"query":"AuraBot"}',
                    },
                },
            ],
        }

        self.assertEqual(
            parse_tool_calls(response),
            [
                ToolCall("read_file", {"path": "a.txt"}, "call_1"),
                ToolCall("search", {"query": "AuraBot"}, "call_2"),
            ],
        )

    def test_parses_json_content_from_complete_response(self):
        response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"tool":['
                            '{"name":"ping","arguments":{}},'
                            '{"name":"status","arguments":{}}]}'
                        )
                    }
                }
            ]
        }

        self.assertEqual(
            parse_tool_calls(response),
            [ToolCall("ping", {}), ToolCall("status", {})],
        )

    def test_parses_json_code_fence_and_singular_alias(self):
        content = """```json
        {"tool": "ping", "arguments": {}}
        ```"""

        self.assertEqual(parse_tool_call(content), [ToolCall("ping", {})])

    def test_rejects_malformed_arguments(self):
        response = {
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": "not-json",
                    },
                }
            ]
        }

        with self.assertRaisesRegex(ToolCallParseError, "arguments"):
            parse_tool_calls(response)

    def test_rejects_non_object_arguments(self):
        content = '{"tool":"search","arguments":["AuraBot"]}'

        with self.assertRaisesRegex(ToolCallParseError, "JSON object"):
            parse_tool_calls(content)

    def test_rejects_entire_batch_when_one_call_is_invalid(self):
        content = {
            "tool": [
                {"name": "ping", "arguments": {}},
                {"name": "broken", "arguments": "{"},
            ]
        }

        with self.assertRaisesRegex(ToolCallParseError, "index 1"):
            parse_tool_calls(content)

    def test_rejects_malformed_json_protocol_but_not_plain_text(self):
        with self.assertRaisesRegex(ToolCallParseError, "JSON is invalid"):
            parse_tool_calls('{"tool":')

    def test_rejects_invalid_input_type(self):
        with self.assertRaisesRegex(TypeError, "string, mapping, or message list"):
            parse_tool_calls(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
