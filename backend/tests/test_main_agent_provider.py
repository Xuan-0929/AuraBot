"""验证生产 Main LLM 的惰性 OpenAI-compatible 流式边界。

测试用内存 HTTP client 代替网络，真实穿过 SSE 解码与 ToolCallAssembler，确保工具调用
delta、结束帧和请求 schema 被归一化为 Main Agent 既有 Provider chunk 协议；缺少密钥时
必须静默返回且连 client 都不构造。
"""

from __future__ import annotations

import unittest
import tempfile
import uuid
import os
import concurrent.futures
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.main import create_app
from backend.main_agent.provider import OpenAICompatibleProvider, build_chat_provider
from backend.main_agent.realtime import RealtimeHub
from backend.main_agent.settings import ChatModelSettings, MainAgentSettings
from backend.main_agent.observations import SafeWebReader, TavilySearchExecutor
from backend.main_agent.profiles import DEFAULT_SOUL
from backend.main_agent.tool_runtime import ToolCallAssembler


class _Response:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def raise_for_status(self) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _Stream:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


class _Client:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.requests: list[tuple[str, str, dict]] = []
        self.closed = False

    def stream(self, method: str, url: str, **kwargs) -> _Stream:
        self.requests.append((method, url, kwargs))
        return _Stream(_Response(self.lines))

    async def aclose(self) -> None:
        self.closed = True


class OpenAICompatibleProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_tool_call_deltas_cross_the_existing_provider_protocol(self) -> None:
        client = _Client([
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"say-1",'
            '"function":{"name":"say_to_user","arguments":"{\\"text\\":\\"hello"}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":" world\\"}"}}]},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ])
        settings = ChatModelSettings(
            base_url="https://model.example/v1",
            model_name="chat-model",
            api_key="secret",
            timeout_seconds=12.0,
        )
        provider = OpenAICompatibleProvider(settings, client_factory=lambda: client)
        surface = SimpleNamespace(provider_schemas=lambda: ({
            "type": "function", "function": {"name": "say_to_user"},
        },))
        request = SimpleNamespace(
            snapshot=SimpleNamespace(prompt='{"conversation":[{"role":"user","content":"hi"}]}', soul="可靠、简短"),
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
        self.assertEqual(kwargs["json"]["model"], "chat-model")
        messages = kwargs["json"]["messages"]
        self.assertEqual([message["role"] for message in messages], ["system", "system", "user"])
        self.assertIn("say_to_user", messages[0]["content"])
        self.assertIn("PREPARE", messages[0]["content"])
        self.assertIn("RESPOND", messages[0]["content"])
        self.assertIn("CONTINUE", messages[0]["content"])
        self.assertEqual(messages[1], {"role": "system", "content": "可靠、简短"})
        self.assertEqual(messages[2], {
            "role": "user",
            "content": '{"conversation":[{"role":"user","content":"hi"}]}',
        })
        self.assertEqual(kwargs["json"]["tools"], [{
            "type": "function", "function": {"name": "say_to_user"},
        }])
        await provider.aclose()
        self.assertTrue(client.closed)

    async def test_missing_key_is_unavailable_without_constructing_an_http_client(self) -> None:
        constructed = 0

        def client_factory():
            nonlocal constructed
            constructed += 1
            raise AssertionError("missing-key provider must not construct HTTP transport")

        provider = build_chat_provider(
            ChatModelSettings(
                base_url="https://model.example/v1",
                model_name="chat-model",
                api_key="",
                timeout_seconds=12.0,
            ),
            client_factory=client_factory,
        )
        chunks = [chunk async for chunk in provider(SimpleNamespace())]

        self.assertEqual(chunks, [])
        self.assertEqual(constructed, 0)


class ProductionProviderCompositionTests(unittest.TestCase):
    def test_production_surface_matches_configured_web_executors_and_default_soul(self) -> None:
        class Owner:
            async def startup(self) -> None:
                return None

            async def shutdown(self) -> None:
                return None

        class Memory(Owner):
            async def recall(self, **_kwargs):
                return ()

            async def record_confirmed_input(self, **_kwargs) -> None:
                return None

        for api_key, expected in (("", set()), ("tavily-secret", {"web_search", "web_read"})):
            with self.subTest(api_key=bool(api_key)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                settings = MainAgentSettings.from_config(
                    {
                        "app": {"data_dir": str(root / "data"), "workspaces": []},
                        "main_agent": {"web": {"tavily_api_key": api_key}},
                        "chat_model": {},
                    },
                    root / "config.yaml",
                )
                with patch("backend.main.MainAgentSettings.from_config", return_value=settings):
                    app = create_app(
                        task_manager=Owner(),
                        memory_bridge=Memory(),
                        memory_worker=Owner(),
                    )
                    with TestClient(app):
                        runtime = app.state.main_agent_runtime
                        actual = {
                            name for name in ("web_search", "web_read")
                            if runtime.tool_policy.definition(name) is not None
                        }
                        self.assertEqual(actual, expected)
                        self.assertEqual(
                            {
                                name for name in ("web_search", "web_read")
                                if name in runtime.harness.executors
                            },
                            expected,
                        )
                        if api_key:
                            self.assertIsInstance(runtime.harness.executors["web_search"], TavilySearchExecutor)
                            self.assertIsInstance(runtime.harness.executors["web_read"], SafeWebReader)
                        self.assertEqual(runtime.soul, DEFAULT_SOUL)

    def test_testclient_text_submit_streams_a_say_tool_through_the_real_runtime(self) -> None:
        class Owner:
            async def startup(self) -> None:
                return None

            async def shutdown(self) -> None:
                return None

        class Memory(Owner):
            async def recall(self, **_kwargs):
                return ()

            async def record_confirmed_input(self, **_kwargs) -> None:
                return None

        origin = "http://127.0.0.1:6400"
        hub = RealtimeHub(allowed_origins={origin})

        def provider_factory(_settings, _metrics):
            def provider(_request):
                return [{"choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "say-production",
                    "function": {
                        "name": "say_to_user",
                        "arguments": '{"text":"production composed"}',
                    },
                }]}}]}]

            return provider

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            workspace.mkdir()
            settings = MainAgentSettings.from_config({
                "app": {
                    "data_dir": str(root / "data"),
                    "workspaces": [{"id": "workspace", "path": str(workspace)}],
                },
                "main_agent": {},
                "chat_model": {
                    "base_url": "https://model.invalid/v1",
                    "model_name": "test-model",
                    "api_key": "test-key",
                },
            }, root / "config.yaml")
            with patch("backend.main.MainAgentSettings.from_config", return_value=settings):
                app = create_app(
                    task_manager=Owner(),
                    realtime_hub=hub,
                    memory_bridge=Memory(),
                    memory_worker=Owner(),
                    provider_factory=provider_factory,
                )
                with TestClient(app) as client:
                    token = client.post(
                        "/api/v1/main-agent/realtime-token",
                        headers={"origin": origin},
                    ).json()["token"]
                    socket_context = client.websocket_connect(
                        "/api/v1/main-agent/realtime",
                        subprotocols=[
                            "aurabot.main-agent.v1",
                            f"aurabot.capability.{token}",
                        ],
                        headers={"origin": origin},
                    )
                    websocket = socket_context.__enter__()
                    try:
                        websocket.send_json({
                            "type": "session.open",
                            "seq": 1,
                            "command_id": str(uuid.uuid4()),
                            "client_id": str(uuid.uuid4()),
                            "mode": "text",
                        })
                        opened = websocket.receive_json()
                        self.assertEqual(opened["type"], "session.opened")
                        websocket.receive_json()
                        websocket.send_json({
                            "type": "text.submit",
                            "seq": 2,
                            "command_id": str(uuid.uuid4()),
                            "text": "hello production",
                        })
                        messages = [websocket.receive_json(), websocket.receive_json()]
                        clause = next(item for item in messages if item["type"] == "clause.offer")
                        self.assertEqual(clause["text"], "production composed")
                        websocket.send_json({
                            "type": "renderer.ack",
                            "seq": 3,
                            "command_id": str(uuid.uuid4()),
                            "renderer_epoch": opened["renderer_epoch"],
                            "clause_id": clause["clause_id"],
                            "complete": True,
                        })
                    finally:
                        try:
                            socket_context.__exit__(None, None, None)
                        except concurrent.futures.CancelledError:
                            pass


if __name__ == "__main__":
    unittest.main()
