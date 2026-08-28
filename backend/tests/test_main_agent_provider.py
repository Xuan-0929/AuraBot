"""Verify the production OpenAI-compatible provider's narrow async streaming boundary.

The tests cross real SSE decoding and ToolCall assembly, assert the fixed control/Soul/Snapshot message
layers, and prove missing configuration is truthfully silent without constructing network transport.
Profile composition, Web tools, and realtime delivery are covered at their own public boundaries.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.main_agent.provider import OpenAICompatibleProvider, build_chat_provider
from backend.main_agent.settings import ChatModelSettings
from backend.main_agent.tool_runtime import ToolCallAssembler


class Response:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class Stream:
    def __init__(self, response: Response) -> None:
        self.response = response

    async def __aenter__(self) -> Response:
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


class Client:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.requests: list[tuple[str, str, dict]] = []
        self.closed = False

    def stream(self, method: str, url: str, **kwargs) -> Stream:
        self.requests.append((method, url, kwargs))
        return Stream(Response(self.lines))

    async def aclose(self) -> None:
        self.closed = True


class OpenAICompatibleProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_deltas_use_control_soul_and_snapshot_messages(self) -> None:
        client = Client([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"say-1",'
            '"function":{"name":"say_to_user","arguments":"{\\"text\\":\\"hello"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":" world\\"}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ])
        settings = ChatModelSettings("https://model.example/v1", "chat-model", "secret", 12.0)
        provider = OpenAICompatibleProvider(settings, client_factory=lambda: client)
        surface = SimpleNamespace(provider_schemas=lambda: ({
            "type": "function", "function": {"name": "say_to_user"},
        },))
        request = SimpleNamespace(
            snapshot=SimpleNamespace(
                prompt='{"conversation":[{"role":"user","content":"hi"}]}', soul="可靠、简短",
            ),
            tool_surface=surface,
        )

        parser = ToolCallAssembler()
        async for chunk in provider(request):
            parser.feed(chunk)
        calls = parser.finish()

        self.assertEqual([(call.name, call.arguments) for call in calls], [
            ("say_to_user", {"text": "hello world"}),
        ])
        method, url, kwargs = client.requests[0]
        self.assertEqual((method, url), ("POST", "https://model.example/v1/chat/completions"))
        messages = kwargs["json"]["messages"]
        self.assertEqual([message["role"] for message in messages], ["system", "system", "user"])
        self.assertIn("say_to_user", messages[0]["content"])
        self.assertIn("PREPARE", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "可靠、简短")
        self.assertNotIn("可靠、简短", messages[2]["content"])
        await provider.aclose()
        self.assertTrue(client.closed)

    async def test_missing_model_configuration_never_constructs_http(self) -> None:
        constructed = 0

        def client_factory():
            nonlocal constructed
            constructed += 1
            raise AssertionError("unavailable provider must not construct transport")

        provider = build_chat_provider(
            ChatModelSettings("https://model.example/v1", "chat-model", "", 12.0),
            client_factory=client_factory,
        )
        self.assertEqual([chunk async for chunk in provider(SimpleNamespace())], [])
        self.assertEqual(constructed, 0)
