"""
验证 BackendRuntime 的集中依赖所有权、启动、关闭与幂等生命周期。

测试组合真实 managers 和 Task runtime，隔离外部网络，不覆盖 FastAPI route 序列化。
"""

import asyncio
import unittest

from backend.app.runtime import BackendRuntime, RuntimeSettings


RUNTIME_CONFIG = {
    "local_mcp_server": "http://127.0.0.1:6410/mcp",
    "task_model": {
        "base_url": "https://example.invalid/v1",
        "api_key": "test",
        "model_name": "task-model",
    },
    "task_runtime": {
        "max_running": 3,
        "default_timeout_ms": 123_000,
        "max_timeout_ms": 456_000,
        "terminal_retention_seconds": 7,
        "sse_subscriber_queue_size": 11,
        "sse_keepalive_seconds": 9,
        "shutdown_grace_seconds": 0,
    },
}


class FakeLLMClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


class BackendRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_settings_have_stable_defaults(self):
        settings = RuntimeSettings.from_mapping(None)

        self.assertEqual(settings.max_running, 8)
        self.assertEqual(settings.default_timeout_ms, 300_000)
        self.assertEqual(settings.max_timeout_ms, 1_800_000)

    async def test_runtime_is_the_composition_root_for_one_application(self):
        client = FakeLLMClient()

        runtime = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=client,
        )

        self.assertIs(runtime.task_llm_client, client)
        self.assertIs(
            runtime.harness_factory.task_llm_client,
            runtime.task_llm_client,
        )
        self.assertEqual(runtime.task_manager.running_count, 0)
        self.assertEqual(runtime.task_manager.queued_count, 0)
        self.assertEqual(runtime.settings.max_running, 3)
        self.assertEqual(runtime.settings.sse_keepalive_seconds, 9)
        self.assertIs(
            runtime.task_service.task_manager,
            runtime.task_manager,
        )
        self.assertIs(
            runtime.plan_tool_service.task_manager,
            runtime.task_manager,
        )

        await runtime.aclose()
        self.assertTrue(client.closed)

    async def test_each_application_gets_distinct_manager_instances(self):
        first = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=FakeLLMClient(),
        )
        second = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=FakeLLMClient(),
        )

        self.assertIsNot(first.task_manager, second.task_manager)
        self.assertIsNot(first.tool_group_manager, second.tool_group_manager)
        self.assertIsNot(first.skill_group_manager, second.skill_group_manager)

        await first.aclose()
        await second.aclose()

    async def test_plan_tool_group_is_lazy_in_runtime_manager(self):
        runtime = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=FakeLLMClient(),
        )

        self.assertIsNone(runtime.tool_group_manager.get_cached("Plan"))

        session = runtime.capability_sessions.create()
        group = session.acquire_tool_groups(("Plan",))[0]

        self.assertEqual(group.name, "Plan")
        self.assertTrue(
            all(
                tool.server_url == RUNTIME_CONFIG["local_mcp_server"]
                for tool in group.tools
            )
        )
        session.close()
        self.assertIsNone(runtime.tool_group_manager.get_cached("Plan"))
        await runtime.aclose()

    async def test_runtime_closes_sessions_created_outside_task_manager(self):
        runtime = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=FakeLLMClient(),
        )
        session = runtime.capability_sessions.create()
        session.acquire_tool_groups(("Plan",))

        await runtime.aclose()

        self.assertTrue(runtime.capability_sessions.closed)
        self.assertTrue(session.closed)
        self.assertEqual(runtime.tool_group_manager.reference_count("Plan"), 0)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            runtime.tool_group_manager.acquire(("Plan",))

    async def test_runtime_close_continues_after_caller_cancellation(self):
        class BlockingCloseLLM(FakeLLMClient):
            def __init__(self):
                super().__init__()
                self.started = asyncio.Event()
                self.release = asyncio.Event()

            async def aclose(self):
                self.started.set()
                await self.release.wait()
                await super().aclose()

        client = BlockingCloseLLM()
        runtime = BackendRuntime.from_config(
            RUNTIME_CONFIG,
            task_llm_client=client,
        )
        closing = asyncio.create_task(runtime.aclose())
        await client.started.wait()

        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        client.release.set()
        await runtime.aclose()

        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
