import json
import re
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.agent.tools.core.ToolCallEntity import (
    FinalResponse,
    ToolCallBatch,
    ToolExecutionResult,
)
from backend.agent.tools.core.ToolCallProtocol import ToolCallProtocol
from backend.agent.tools.core.ToolExceptions import ToolCallParseError


def call_number(call_id: str) -> int:
    match = re.fullmatch(r"call_(\d+)", call_id)
    if match is None:
        raise AssertionError(f"unexpected call ID: {call_id}")
    return int(match.group(1))


class ToolCallProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = ToolCallProtocol()

    def test_parses_final_response_and_preserves_content(self):
        response = self.protocol.parse(
            '{"type":"final","content":"  Complete.  "}'
        )

        self.assertEqual(response, FinalResponse("  Complete.  "))

    def test_parses_single_call_and_assigns_runtime_id(self):
        response = self.protocol.parse(
            '{"type":"tool_calls","calls":['
            '{"name":"read_file","arguments":{"path":"a.txt"}}]}'
        )

        self.assertIsInstance(response, ToolCallBatch)
        self.assertEqual(response.calls[0].name, "read_file")
        self.assertEqual(response.calls[0].arguments, {"path": "a.txt"})
        self.assertRegex(response.calls[0].call_id, r"^call_\d+$")

    def test_parses_multiple_calls_with_consecutive_ids(self):
        response = self.protocol.parse(
            '{"type":"tool_calls","calls":['
            '{"name":"read_file","arguments":{"path":"a.txt"}},'
            '{"name":"search","arguments":{"query":"AuraBot"}}]}'
        )

        self.assertIsInstance(response, ToolCallBatch)
        self.assertEqual(
            [call.name for call in response.calls],
            ["read_file", "search"],
        )
        numbers = [call_number(call.call_id) for call in response.calls]
        self.assertEqual(numbers[1], numbers[0] + 1)

    def test_encodes_ordered_observation(self):
        results = [
            ToolExecutionResult(
                tool_name="search",
                call_id="call_20",
                content=[{"type": "text", "text": "found"}],
                structured_content={"count": 1},
                is_error=False,
            ),
            ToolExecutionResult(
                tool_name="read_file",
                call_id="call_21",
                content=[{"type": "text", "text": "failed"}],
                structured_content={"error": {"code": "tool_error"}},
                is_error=True,
                error_code="tool_error",
            ),
        ]

        encoded = self.protocol.encode_observation(results)

        self.assertEqual(
            json.loads(encoded),
            {
                "type": "tool_observation",
                "results": [
                    {
                        "id": "call_20",
                        "name": "search",
                        "is_error": False,
                        "content": [{"type": "text", "text": "found"}],
                        "structured_content": {"count": 1},
                        "error_code": None,
                    },
                    {
                        "id": "call_21",
                        "name": "read_file",
                        "is_error": True,
                        "content": [{"type": "text", "text": "failed"}],
                        "structured_content": {
                            "error": {"code": "tool_error"}
                        },
                        "error_code": "tool_error",
                    },
                ],
            },
        )

    def test_rejects_invalid_json_and_markdown_fence(self):
        cases = [
            '{"type":"final",',
            '```json\n{"type":"final","content":"done"}\n```',
            'prefix {"type":"final","content":"done"}',
            "The task is complete.",
        ]

        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ToolCallParseError) as raised:
                    self.protocol.parse(text)
                self.assertEqual(raised.exception.code, "invalid_json")

    def test_rejects_unknown_type_and_non_object_envelope(self):
        cases = [
            ('{"type":"answer","content":"done"}', "invalid_envelope"),
            ('[]', "invalid_envelope"),
        ]

        for text, code in cases:
            with self.subTest(text=text):
                with self.assertRaises(ToolCallParseError) as raised:
                    self.protocol.parse(text)
                self.assertEqual(raised.exception.code, code)

    def test_rejects_invalid_final(self):
        cases = [
            '{"type":"final","content":""}',
            '{"type":"final","content":1}',
            '{"type":"final","content":"done","extra":true}',
        ]

        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ToolCallParseError) as raised:
                    self.protocol.parse(text)
                self.assertEqual(raised.exception.code, "invalid_final")

    def test_rejects_empty_or_non_array_calls(self):
        cases = [
            '{"type":"tool_calls","calls":[]}',
            '{"type":"tool_calls","calls":{}}',
            '{"type":"tool_calls","calls":[],"extra":true}',
        ]

        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(ToolCallParseError) as raised:
                    self.protocol.parse(text)
                self.assertEqual(raised.exception.code, "invalid_calls")

    def test_rejects_invalid_call_shape_name_arguments_and_model_id(self):
        raw_calls = [
            {"name": "search", "arguments": [],},
            {"name": " search", "arguments": {}},
            {"name": "", "arguments": {}},
            {"name": "search", "arguments": {}, "id": "model-id"},
            {"tool": "search", "arguments": {}},
        ]

        for raw_call in raw_calls:
            with self.subTest(raw_call=raw_call):
                text = json.dumps({"type": "tool_calls", "calls": [raw_call]})
                with self.assertRaises(ToolCallParseError) as raised:
                    self.protocol.parse(text)
                self.assertEqual(raised.exception.code, "invalid_call")

    def test_invalid_batch_is_atomic_and_does_not_consume_id(self):
        before = self.protocol.parse(
            '{"type":"tool_calls","calls":[{"name":"before","arguments":{}}]}'
        )
        with self.assertRaises(ToolCallParseError):
            self.protocol.parse(
                '{"type":"tool_calls","calls":['
                '{"name":"valid","arguments":{}},'
                '{"name":"broken","arguments":[]}]}'
            )
        after = self.protocol.parse(
            '{"type":"tool_calls","calls":[{"name":"after","arguments":{}}]}'
        )

        self.assertIsInstance(before, ToolCallBatch)
        self.assertIsInstance(after, ToolCallBatch)
        self.assertEqual(
            call_number(after.calls[0].call_id),
            call_number(before.calls[0].call_id) + 1,
        )

    def test_concurrent_protocol_instances_allocate_unique_batch_ids(self):
        text = (
            '{"type":"tool_calls","calls":['
            '{"name":"one","arguments":{}},'
            '{"name":"two","arguments":{}},'
            '{"name":"three","arguments":{}}]}'
        )

        def parse_batch(_: int) -> tuple[int, ...]:
            response = ToolCallProtocol().parse(text)
            if not isinstance(response, ToolCallBatch):
                raise AssertionError("expected ToolCallBatch")
            return tuple(call_number(call.call_id) for call in response.calls)

        with ThreadPoolExecutor(max_workers=8) as executor:
            batches = list(executor.map(parse_batch, range(64)))

        all_ids = [call_id for batch in batches for call_id in batch]
        self.assertEqual(len(all_ids), len(set(all_ids)))
        for batch in batches:
            self.assertEqual(batch, tuple(range(batch[0], batch[0] + 3)))

    def test_rejects_invalid_input_type(self):
        with self.assertRaisesRegex(TypeError, "must be a string"):
            self.protocol.parse({"type": "final"})  # type: ignore[arg-type]

    def test_rejects_empty_or_duplicate_observation_ids(self):
        empty_id = ToolExecutionResult(
            tool_name="search",
            call_id="",
            content=[],
            structured_content=None,
            is_error=False,
        )
        duplicate = ToolExecutionResult(
            tool_name="search",
            call_id="call_1",
            content=[],
            structured_content=None,
            is_error=False,
        )

        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            self.protocol.encode_observation([])
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            self.protocol.encode_observation([empty_id])
        with self.assertRaisesRegex(ValueError, "must be unique"):
            self.protocol.encode_observation([duplicate, duplicate])


if __name__ == "__main__":
    unittest.main()
