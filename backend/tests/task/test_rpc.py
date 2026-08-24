"""验证 Pi RPC 的严格 JSONL、response 关联、有界事件与文本合并。

测试用内存 StreamReader/Writer 驱动协议，不启动进程；stderr 已交给操作系统 DEVNULL，
因此每个 active Task 在 Python 中只应存在一个 stdout reader。
"""

from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any

from backend.task.rpc import PiCommandError, PiProtocolError, PiRpcClient


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


class PiRpcClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stdout = asyncio.StreamReader(limit=4096)
        self.stdin = RecordingWriter()
        self.client = PiRpcClient(
            stdin=self.stdin,
            stdout=self.stdout,
            response_timeout_seconds=1,
            max_line_bytes=1024,
            event_queue_size=16,
            output_chunk_bytes=16,
        )
        await self.client.start()

    async def asyncTearDown(self):
        self.stdout.feed_eof()
        await self.client.aclose()

    async def written(self, count: int = 1) -> dict[str, Any]:
        for _ in range(100):
            if len(self.stdin.lines()) >= count:
                return self.stdin.lines()[count - 1]
            await asyncio.sleep(0)
        self.fail("RPC command was not written")

    async def test_correlates_success_and_failure_by_generated_id(self):
        success = asyncio.create_task(self.client.command("get_state", {"probe": True}))
        self.assertEqual(
            await self.written(),
            {"id": "cmd-1", "type": "get_state", "probe": True},
        )
        self.stdout.feed_data(
            b'{"type":"response","id":"cmd-1","success":true,'
            b'"data":{"sessionId":"s"}}\n'
        )
        self.assertEqual((await success)["data"]["sessionId"], "s")

        failure = asyncio.create_task(self.client.command("prompt", {"message": "x"}))
        self.assertEqual((await self.written(2))["id"], "cmd-2")
        self.stdout.feed_data(
            b'{"type":"response","id":"cmd-2","success":false,'
            b'"error":"model unavailable"}\n'
        )
        with self.assertRaisesRegex(PiCommandError, "model unavailable"):
            await failure

    async def test_merges_unicode_text_and_preserves_event_order(self):
        self.stdout.feed_data(
            ('{"type":"message_update","assistantMessageEvent":'
             '{"type":"text_delta","delta":"a\u2028b\u2029"}}\n'
             '{"type":"message_update","assistantMessageEvent":'
             '{"type":"text_delta","delta":"tail"}}\n'
             '{"type":"agent_end","willRetry":false}\n'
             '{"type":"agent_settled"}\n').encode()
        )
        events = self.client.events()
        self.assertEqual((await anext(events)).payload, {"text": "a\u2028b\u2029tail"})
        self.assertEqual((await anext(events)).type, "agent_end")
        self.assertEqual((await anext(events)).type, "agent_settled")

    async def test_splits_text_on_unicode_safe_byte_boundary(self):
        self.stdout.feed_data(
            b'{"type":"message_update","assistantMessageEvent":'
            b'{"type":"text_delta","delta":"1234567890"}}\n'
            b'{"type":"message_update","assistantMessageEvent":'
            b'{"type":"text_delta","delta":"abcdefghij"}}\n'
            b'{"type":"agent_settled"}\n'
        )
        events = self.client.events()
        self.assertEqual((await anext(events)).payload["text"], "1234567890abcdef")
        self.assertEqual((await anext(events)).payload["text"], "ghij")
        self.assertEqual((await anext(events)).type, "agent_settled")

    async def test_writes_extension_ui_value_and_cancel(self):
        await self.client.send_extension_ui_response("ui-1", "answer")
        await self.client.send_extension_ui_response("ui-2", None)
        self.assertEqual(
            self.stdin.lines(),
            [
                {"type": "extension_ui_response", "id": "ui-1", "value": "answer"},
                {"type": "extension_ui_response", "id": "ui-2", "cancelled": True},
            ],
        )

    async def test_rejects_bad_framing_and_oversized_lines(self):
        cases = (
            (b'\xff\n', "UTF-8"),
            (b'[]\n', "object"),
            (b'{"value":1}\n', "type"),
            (b'{"type":"agent_start","x":"' + b"x" * 1100 + b'"}\n', "1024"),
        )
        for data, pattern in cases:
            with self.subTest(pattern=pattern):
                stdout = asyncio.StreamReader(limit=4096)
                client = PiRpcClient(
                    stdin=RecordingWriter(), stdout=stdout,
                    response_timeout_seconds=1, max_line_bytes=1024,
                    event_queue_size=4, output_chunk_bytes=16,
                )
                await client.start()
                stdout.feed_data(data)
                with self.assertRaisesRegex(PiProtocolError, pattern):
                    await anext(client.events())
                stdout.feed_eof()
                await client.aclose()

    async def test_rejects_incomplete_eof_unknown_response_and_pending_eof(self):
        stdout = asyncio.StreamReader(limit=4096)
        client = PiRpcClient(
            stdin=RecordingWriter(), stdout=stdout,
            response_timeout_seconds=1, max_line_bytes=1024,
            event_queue_size=4, output_chunk_bytes=16,
        )
        await client.start()
        stdout.feed_data(b'{"type":"agent_start"}')
        stdout.feed_eof()
        with self.assertRaisesRegex(PiProtocolError, "incomplete"):
            await anext(client.events())
        await client.aclose()

        self.stdout.feed_data(b'{"type":"response","id":"never","success":true}\n')
        with self.assertRaisesRegex(PiProtocolError, "unknown response id"):
            await anext(self.client.events())

        stdout = asyncio.StreamReader(limit=4096)
        pending_client = PiRpcClient(
            stdin=RecordingWriter(), stdout=stdout,
            response_timeout_seconds=1, max_line_bytes=1024,
            event_queue_size=4, output_chunk_bytes=16,
        )
        await pending_client.start()
        pending = asyncio.create_task(pending_client.command("get_state"))
        await asyncio.sleep(0)
        stdout.feed_eof()
        with self.assertRaisesRegex(PiProtocolError, "pending"):
            await pending
        await pending_client.aclose()


if __name__ == "__main__":
    unittest.main()
