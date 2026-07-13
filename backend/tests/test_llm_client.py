import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from backend.agent import LLMClient as ExportedLLMClient
from backend.agent.LLMClient import LLMClient


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


class LLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def _collect_stream(self, stream):
        chunks = []

        async for chunk in stream:
            chunks.append(chunk)

        return chunks

    def test_exports_llm_client_from_agent_package(self):
        self.assertIs(ExportedLLMClient, LLMClient)

    def test_rejects_invalid_concurrency_limit(self):
        with self.assertRaisesRegex(ValueError, "at least 1"):
            LLMClient(
                base_url="https://api.example.com/v1",
                api_key="test-key",
                model="test-model",
                max_concurrency=0,
            )

    def test_chunk_invoke_keeps_blocking_compatibility_api(self):
        response = httpx.Response(
            200,
            request=httpx.Request(
                "POST",
                "https://api.example.com/v1/chat/completions",
            ),
            json={"choices": [{"message": {"content": "sync reply"}}]},
        )
        messages = [{"role": "user", "content": "hello"}]

        with patch("backend.agent.LLMClient.httpx.post", return_value=response) as post:
            client = LLMClient(
                base_url="https://api.example.com/v1",
                api_key="test-key",
                model="test-model",
            )
            result = client.chunk_invoke(messages)

        self.assertEqual(result, "sync reply")
        body = json.loads(post.call_args.kwargs["content"].decode("utf-8"))
        self.assertEqual(body["messages"], messages)
        asyncio.run(client.aclose())

    async def test_invoke_posts_openai_compatible_request(self):
        requests = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": "hello from model"}}
                    ]
                },
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)
        messages = [{"role": "user", "content": "hello"}]

        result = await client.invoke(messages)

        self.assertEqual(result, "hello from model")
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://api.example.com/v1/chat/completions",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer test-key")
        body = json.loads(request.content.decode("utf-8"))
        self.assertEqual(body["model"], "test-model")
        self.assertEqual(body["messages"], messages)
        self.assertNotIn("stream", body)

    async def test_misspelled_async_alias_uses_shared_async_client(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "alias reply"}}]},
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        result = await client.aysnc_invoke(
            [{"role": "user", "content": "hello"}]
        )

        self.assertEqual(result, "alias reply")

    async def test_one_client_serves_multiple_agent_loops_concurrently(self):
        active_requests = 0
        maximum_active_requests = 0
        both_started = asyncio.Event()
        release_requests = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_requests, maximum_active_requests
            active_requests += 1
            maximum_active_requests = max(
                maximum_active_requests,
                active_requests,
            )

            if active_requests == 2:
                both_started.set()

            try:
                await release_requests.wait()
                prompt = json.loads(request.content)["messages"][0]["content"]
                return httpx.Response(
                    200,
                    json={
                        "choices": [
                            {"message": {"content": f"reply:{prompt}"}}
                        ]
                    },
                )
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

        self.assertEqual(set(results), {"reply:task-a", "reply:task-b"})
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
            maximum_active_requests = max(
                maximum_active_requests,
                active_requests,
            )

            try:
                if request_count == 1:
                    first_started.set()
                    await release_first.wait()

                return httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )
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

    async def test_stream_posts_streaming_request_and_yields_deltas(self):
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

    async def test_raises_compatible_error_for_http_status(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="rate limited")

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaisesRegex(RuntimeError, "status 429"):
            await client.invoke([{"role": "user", "content": "hello"}])

    async def test_raises_when_non_streaming_response_has_no_content(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": ""}}]},
            )

        client = make_client(handler)
        self.addAsyncCleanup(client.aclose)

        with self.assertRaisesRegex(ValueError, "did not include content"):
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
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )

        client = make_client(handler)

        async with client:
            self.assertEqual(
                await client.invoke([{"role": "user", "content": "hello"}]),
                "ok",
            )

        self.assertTrue(client._http_client.is_closed)


if __name__ == "__main__":
    unittest.main()
