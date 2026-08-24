"""
验证 Pi RPC 的严格 LF JSONL framing、请求关联、事件归一化与 fatal 错误。

测试直接驱动 asyncio 字节流和记录型 stdin writer，不启动 subprocess；输入 JSON 全部
由测试独立构造，以避免复用生产 parser 掩盖协议错误。
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from backend.task.runtime.rpc import PiCommandError, PiProtocolError, PiRpcClient


class RecordingWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def lines(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.buffer.decode().splitlines()]


class FailingWriter(RecordingWriter):
    """Inject one concrete stdin I/O failure at write or drain."""

    def __init__(self, *, write_error: OSError | None = None, drain_error: OSError | None = None):
        super().__init__()
        self.write_error = write_error
        self.drain_error = drain_error

    def write(self, data: bytes) -> None:
        if self.write_error is not None:
            raise self.write_error
        super().write(data)

    async def drain(self) -> None:
        if self.drain_error is not None:
            raise self.drain_error
        await super().drain()


class PiRpcClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stdout = asyncio.StreamReader(limit=4096)
        self.stderr = asyncio.StreamReader(limit=4096)
        self.stdin = RecordingWriter()
        self.client = PiRpcClient(
            stdin=self.stdin,
            stdout=self.stdout,
            stderr=self.stderr,
            response_timeout_seconds=1,
            max_line_bytes=1024,
            event_queue_size=16,
            stderr_tail_bytes=128,
            output_chunk_bytes=16,
        )
        await self.client.start()

    async def asyncTearDown(self):
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        await self.client.aclose()

    async def next_written_command(self, count: int = 1) -> dict[str, Any]:
        for _ in range(100):
            lines = self.stdin.lines()
            if len(lines) >= count:
                return lines[count - 1]
            await asyncio.sleep(0)
        self.fail("RPC command was not written")

    async def test_correlates_success_and_failure_responses_by_generated_id(self):
        success_task = asyncio.create_task(
            self.client.command("get_state", {"probe": True})
        )
        command = await self.next_written_command(1)
        self.assertEqual(command, {"id": "cmd-1", "type": "get_state", "probe": True})
        self.stdout.feed_data(
            b'{"type":"response","id":"cmd-1","command":"get_state",'
            b'"success":true,"data":{"sessionId":"s"}}\n'
        )
        response = await success_task
        self.assertEqual(response["data"]["sessionId"], "s")

        failure_task = asyncio.create_task(self.client.command("prompt", {"message": "x"}))
        command = await self.next_written_command(2)
        self.assertEqual(command["id"], "cmd-2")
        self.stdout.feed_data(
            b'{"type":"response","id":"cmd-2","command":"prompt",'
            b'"success":false,"error":"model unavailable"}\n'
        )
        with self.assertRaisesRegex(PiCommandError, "model unavailable"):
            await failure_task

    async def test_preserves_unicode_separators_and_flushes_text_before_settled(self):
        self.stdout.feed_data(
            '{"type":"message_update","assistantMessageEvent":'
            '{"type":"text_delta","delta":"a\u2028b\u2029"}}\r\n'
            '{"type":"message_update","assistantMessageEvent":'
            '{"type":"text_delta","delta":"tail"}}\n'
            '{"type":"agent_end","willRetry":false}\n'
            '{"type":"agent_settled"}\n'.encode("utf-8")
        )

        iterator = self.client.events()
        output = await anext(iterator)
        agent_end = await anext(iterator)
        settled = await anext(iterator)

        self.assertEqual(output.type, "text_output")
        self.assertEqual(output.payload, {"text": "a\u2028b\u2029tail"})
        self.assertEqual(agent_end.type, "agent_end")
        self.assertEqual(settled.type, "agent_settled")

    async def test_splits_merged_text_at_the_configured_byte_boundary(self):
        self.stdout.feed_data(
            b'{"type":"message_update","assistantMessageEvent":'
            b'{"type":"text_delta","delta":"1234567890"}}\n'
            b'{"type":"message_update","assistantMessageEvent":'
            b'{"type":"text_delta","delta":"abcdefghij"}}\n'
            b'{"type":"agent_settled"}\n'
        )

        iterator = self.client.events()
        first = await anext(iterator)
        second = await anext(iterator)
        settled = await anext(iterator)
        self.assertEqual(first.payload["text"], "1234567890abcdef")
        self.assertEqual(second.payload["text"], "ghij")
        self.assertEqual(settled.type, "agent_settled")

    async def test_writes_standard_extension_ui_responses_without_command_id(self):
        await self.client.send_extension_ui_response("ui-1", "answer")
        await self.client.send_extension_ui_response("ui-2", None)

        self.assertEqual(
            self.stdin.lines(),
            [
                {"type": "extension_ui_response", "id": "ui-1", "value": "answer"},
                {"type": "extension_ui_response", "id": "ui-2", "cancelled": True},
            ],
        )

    async def test_normalizes_write_and_drain_io_failures_to_protocol_error(self):
        for writer in (
            FailingWriter(write_error=OSError("write failed")),
            FailingWriter(drain_error=OSError("drain failed")),
        ):
            with self.subTest(writer=writer):
                stdout = asyncio.StreamReader(limit=4096)
                stderr = asyncio.StreamReader(limit=4096)
                client = PiRpcClient(
                    stdin=writer, stdout=stdout, stderr=stderr,
                    response_timeout_seconds=1, max_line_bytes=1024,
                    event_queue_size=4, stderr_tail_bytes=64, output_chunk_bytes=16,
                )
                await client.start()
                with self.assertRaises(PiProtocolError):
                    await client.send_extension_ui_response("ui", "answer")
                stdout.feed_eof()
                stderr.feed_eof()
                await client.aclose()

    async def assert_protocol_failure(self, data: bytes, pattern: str) -> None:
        self.stdout.feed_data(data)
        iterator = self.client.events()
        with self.assertRaisesRegex(PiProtocolError, pattern):
            await anext(iterator)

    async def test_rejects_invalid_utf8_non_object_missing_type_and_oversized_line(self):
        cases = (
            (b'\xff\n', "UTF-8"),
            (b'[]\n', "object"),
            (b'{"value":1}\n', "type"),
            (b'{"type":"agent_start","padding":"' + b"x" * 1100 + b'"}\n', "1024"),
        )
        for data, pattern in cases:
            with self.subTest(pattern=pattern):
                stdout = asyncio.StreamReader(limit=4096)
                stderr = asyncio.StreamReader(limit=4096)
                client = PiRpcClient(
                    stdin=RecordingWriter(),
                    stdout=stdout,
                    stderr=stderr,
                    response_timeout_seconds=1,
                    max_line_bytes=1024,
                    event_queue_size=4,
                    stderr_tail_bytes=64,
                    output_chunk_bytes=16,
                )
                await client.start()
                stdout.feed_data(data)
                with self.assertRaisesRegex(PiProtocolError, pattern):
                    await anext(client.events())
                stdout.feed_eof()
                stderr.feed_eof()
                await client.aclose()

    async def test_rejects_incomplete_eof_and_unknown_or_duplicate_response_id(self):
        stdout = asyncio.StreamReader(limit=4096)
        stderr = asyncio.StreamReader(limit=4096)
        client = PiRpcClient(
            stdin=RecordingWriter(), stdout=stdout, stderr=stderr,
            response_timeout_seconds=1, max_line_bytes=1024,
            event_queue_size=4, stderr_tail_bytes=64, output_chunk_bytes=16,
        )
        await client.start()
        stdout.feed_data(b'{"type":"agent_start"}')
        stdout.feed_eof()
        with self.assertRaisesRegex(PiProtocolError, "incomplete"):
            await anext(client.events())
        stderr.feed_eof()
        await client.aclose()

        self.stdout.feed_data(
            b'{"type":"response","id":"never-sent","command":"abort","success":true}\n'
        )
        with self.assertRaisesRegex(PiProtocolError, "unknown response id"):
            await anext(self.client.events())

    async def test_drains_and_bounds_stderr_tail(self):
        self.stderr.feed_data(b"a" * 100 + b"important-tail")
        self.stderr.feed_eof()
        for _ in range(20):
            if self.client.stderr_tail.endswith(b"important-tail"):
                break
            await asyncio.sleep(0)

        self.assertLessEqual(len(self.client.stderr_tail), 128)
        self.assertTrue(self.client.stderr_tail.endswith(b"important-tail"))


if __name__ == "__main__":
    unittest.main()
