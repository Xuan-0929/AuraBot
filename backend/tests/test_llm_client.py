"""
验证 OpenAI-compatible LLMClient 的请求构造、响应解析与错误处理。

测试在 HTTP transport 边界使用受控响应，不调用真实模型，也不覆盖 Harness 策略。
"""

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from backend.agent import (
    ChatCompletionChoice as ExportedChatCompletionChoice,
)
from backend.agent import LLMClient as ExportedLLMClient
from backend.agent.llm_client import ChatCompletionChoice, LLMClient


def make_client(
    handler,
    *,
    max_concurrency: int = 8,
) -> LLMClient:
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    )
    return LLMClient(
        base_url="https://api.example.com/v1",
        api_key="test-key",
        model="test-model",
        max_concurrency=max_concurrency,
        http_client=http_client,
    )


def text_response(content: str) -> dict:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ]
    }


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_stream(self, stream):
        chunks = []
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    def test_exports_public_client_and_choice(self):
        self.assertIs(ExportedLLMClient, LLMClient)
        self.assertIs(ExportedChatCompletionChoice, ChatCompletionChoice)

    def test_rejects_invalid_concurrency_limit(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            LLMClient(
                base_url="https://api.example.com/v1",
                api_key="test-key",
                model="test-model",
                max_concurrency=0,
            )

    def test_chunk_invoke_sends_native_tools_and_returns_choice(self):
        response = httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://api.example.com/v1/chat/completions",
            ),
            json=text_response("sync reply"),
        )
        messages = [{"role": "user", "content": "hello"}]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "ping",
                    "description": "Ping",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        with patch("backend.agent.llm_client.httpx.post", return_value=response) as post:
            client = LLMClient(
                base_url="https://api.example.com/v1",
                api_key="test-key",
                model="test-model",
            )
            result = client.chunk_invoke(
                messages,
                tools=tools,
                tool_choice="auto",
            )

        self.assertEqual(
            result,
            ChatCompletionChoice(
                message={"role": "assistant", "content": "sync reply"},
                finish_reason="stop",
            ),
        )
        body = json.loads(post.call_args.kwargs["content"].decode("utf-8"))
        self.assertEqual(body["messages"], messages)
        self.assertEqual(body["tools"], tools)
        self.assertEqual(body["tool_choice"], "auto")
        asyncio.run(client.aclose())

    async def test_invoke_posts_plain_openai_chat_request(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=text_response("hello from model"))

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        messages = [{"role": "user", "content": "hello"}]

        result = await client.invoke(messages)

        self.assertEqual(result.message["content"], "hello from model")
        self.assertEqual(result.finish_reason, "stop")
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.content.decode("utf-8"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], messages)
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)
        self.assertNotIn("stream", body)

    async def test_misspelled_async_alias_forwards_tool_options(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=text_response("alias reply"))

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        tools = [{"type": "function", "function": {"name": "ping"}}]

        result = await client.aysnc_invoke(
            [{"role": "user", "content": "hello"}],
            tools=tools,
            tool_choice="auto",
        )

        self.assertEqual(result.message["content"], "alias reply")
        body = json.loads(requests[0].content)
        self.assertEqual(body["tools"], tools)

    async def test_one_client_serves_multiple_agent_loops_concurrently(self):
        active_requests = 0
        maximum_active_requests = 0
        both_started = asyncio.Event()
        release_requests = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_requests, maximum_active_requests
            active_requests += 1
            maximum_active_requests = max(maximum_active_requests, active_requests)
            if active_requests == 2:
                both_started.set()

            try:
                await release_requests.wait()
                prompt = json.loads(request.content)["messages"][0]["content"]
                return httpx.Response(200, json=text_response(f"reply:{prompt}"))
            finally:
                active_requests -= 1

        client = make_client(handler, max_concurrency=2)
        self.addAsyncCleanup(client.aclose)
        first = asyncio.create_task(
            client.invoke([{"role": "user", "content": "task-a"}])
        )
        second = asyncio.create_task(
            client.invoke([{"role": "user", "content": "task-b"}])
        )

        await asyncio.wait_for(both_started.wait(), timeout=1)
        release_requests.set()
        results = await asyncio.gather(first, second)

        self.assertEqual(
            {result.message["content"] for result in results},
            {"reply:task-a", "reply:task-b"},
        )
        self.assertEqual(maximum_active_requests, 2)

    async def test_concurrency_limit_serializes_excess_requests(self):
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        request_count = 0
        active_requests = 0
        maximum_active_requests = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count, active_requests, maximum_active_requests
            request_count += 1
            active_requests += 1
            maximum_active_requests = max(maximum_active_requests, active_requests)
            try:
                if request_count == 1:
                    first_started.set()
                    await release_first.wait()
                return httpx.Response(200, json=text_response("ok"))
            finally:
                active_requests -= 1

        client = make_client(handler, max_concurrency=1)
        self.addAsyncCleanup(client.aclose)
        first = asyncio.create_task(
            client.invoke([{"role": "user", "content": "task-a"}])
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second = asyncio.create_task(
            client.invoke([{"role": "user", "content": "task-b"}])
        )

        await asyncio.sleep(0)
        self.assertEqual(request_count, 1)
        release_first.set()
        await asyncio.gather(first, second)

        self.assertEqual(request_count, 2)
        self.assertEqual(maximum_active_requests, 1)

    async def test_accepts_native_tool_call_choice_with_null_content(self):
        tool_calls = [
            {
                "id": "call_provider_1",
                "type": "function",
                "function": {"name": "first", "arguments": "{}"},
            },
            {
                "id": "call_provider_2",
                "type": "function",
                "function": {"name": "second", "arguments": "{}"},
            },
        ]

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": tool_calls,
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        result = await client.invoke([{"role": "user", "content": "go"}])

        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.message["tool_calls"], tool_calls)
        self.assertIsNone(result.message["content"])

    async def test_rejects_missing_choice_message_or_finish_reason(self):
        payloads = [
            {},
            {"choices": []},
            {"choices": [{"finish_reason": "stop"}]},
            {"choices": [{"message": {"content": "done"}}]},
            {
                "choices": [
                    {
                        "message": {"role": "user", "content": "done"},
                        "finish_reason": "stop",
                    }
                ]
            },
        ]

        for payload in payloads:
            with self.subTest(payload=payload):
                async def handler(request: httpx.Request) -> httpx.Response:
                    return httpx.Response(200, json=payload)

                client = make_client(handler)
                try:
                    with self.assertRaises(ValueError):
                        await client.invoke([{"role": "user", "content": "hello"}])
                finally:
                    await client.aclose()

    async def test_empty_tools_are_omitted_and_tool_choice_requires_tools(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=text_response("ok"))

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        await client.invoke([], tools=[])

        body = json.loads(requests[0].content)
        self.assertNotIn("tools", body)
        self.assertNotIn("tool_choice", body)
        with self.assertRaisesRegex(ValueError, "requires non-empty tools"):
            await client.invoke([], tool_choice="auto")

    async def test_stream_posts_streaming_request_and_yields_text_deltas(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                content=(
                    b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
                    b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
                headers={"content-type": "text/event-stream"},
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        messages = [{"role": "user", "content": "hello"}]

        result = await self._collect_stream(client.stream(messages))

        self.assertEqual(result, ["hel", "lo"])
        body = json.loads(requests[0].content.decode("utf-8"))
        self.assertEqual(body["messages"], messages)
        self.assertTrue(body["stream"])

    async def test_stream_rejects_tool_calls(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("request must not be sent")

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaisesRegex(ValueError, "use invoke"):
            await self._collect_stream(
                client.stream(
                    [{"role": "user", "content": "hello"}],
                    tools=[{"type": "function", "function": {"name": "ping"}}],
                )
            )

    async def test_raises_compatible_error_for_http_status(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaisesRegex(RuntimeError, "status 429"):
            await client.invoke([{"role": "user", "content": "hello"}])

    async def test_raises_when_streaming_response_has_no_content(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"data: [DONE]\n\n",
                headers={"content-type": "text/event-stream"},
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaisesRegex(ValueError, "did not include content"):
            await self._collect_stream(
                client.stream([{"role": "user", "content": "hello"}])
            )

    async def test_async_context_manager_closes_shared_http_client(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=text_response("ok"))

        client = make_client(handler)

        async with client:
            result = await client.invoke([{"role": "user", "content": "hello"}])
            self.assertEqual(result.message["content"], "ok")

        self.assertTrue(client._http_client.is_closed)


if __name__ == "__main__":
    unittest.main()
