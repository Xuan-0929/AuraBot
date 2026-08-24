"""
验证 PiProcessSupervisor 是 subprocess handle 的唯一 owner 并完整回收进程。

测试通过独立 fake_pi_rpc 可执行脚本覆盖 readiness、prompt、事件、错误、graceful stop、
terminate/kill 与 shutdown；不调用真实模型、网络或全局 pi。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.task.runtime.process import (
    PiProcessStartError,
    PiProcessSupervisor,
    StopIntent,
    TaskLaunchSpec,
)
from backend.task.models import TaskErrorCode
from backend.task.runtime.rpc import PiProtocolError
from backend.task.settings import TaskSettings


class PiProcessSupervisorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.fake_pi = (
            Path(__file__).resolve().parents[2] / "fixtures/fake_pi_rpc.py"
        )
        plugin = self.root / "plugin.ts"
        prompt = self.root / "prompt.md"
        plugin.write_text("// fake\n", encoding="utf-8")
        prompt.write_text("prompt\n", encoding="utf-8")
        self.settings = TaskSettings(
            user_data_dir=self.root / "data",
            database_path=self.root / "data/task/task-runtime.sqlite3",
            session_root=self.root / "data/task/sessions",
            pi_home=self.root / "data/pi-agent",
            pi_runtime_dir=self.root,
            node_executable=sys.executable,
            pi_cli_path=self.fake_pi,
            plugin_entry_path=plugin,
            system_prompt_path=prompt,
            concurrency_limit=2,
            default_timeout_seconds=30,
            startup_timeout_seconds=0.2,
            rpc_timeout_seconds=0.2,
            abort_grace_seconds=0.1,
            terminate_grace_seconds=0.1,
            shutdown_timeout_seconds=1,
            waiting_process_grace_seconds=0.2,
            max_jsonl_line_bytes=1024,
            protocol_queue_size=16,
            stderr_tail_bytes=128,
            output_chunk_bytes=16,
        )
        self.supervisor = PiProcessSupervisor(self.settings)

    async def asyncTearDown(self):
        await self.supervisor.shutdown(1)
        self.temp.cleanup()

    def spec(self) -> TaskLaunchSpec:
        task_id = uuid4()
        return TaskLaunchSpec(
            task_id=task_id,
            title="Test Task",
            prompt="Do the work",
            working_directory=self.project,
        )

    async def test_start_uses_isolated_home_absolute_cli_and_returns_events(self):
        spec = self.spec()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}):
            running = await self.supervisor.start_task(spec)

        state = running.ready_state["data"]
        self.assertEqual(state["sessionId"], str(spec.task_id))
        self.assertEqual(Path(state["cwd"]), self.project)
        self.assertEqual(Path(state["piHome"]), self.settings.pi_home)
        self.assertEqual(state["telemetry"], "0")
        self.assertEqual(state["versionCheck"], "1")
        self.assertIn("--mode", state["argv"])
        self.assertIn(str(self.settings.plugin_entry_path), state["argv"])
        self.assertEqual(await self.supervisor.get(spec.task_id), running)

        iterator = running.rpc.events()
        output = await anext(iterator)
        settled = await anext(iterator)
        self.assertEqual(output.payload["text"], "done")
        self.assertEqual(settled.type, "agent_settled")
        self.assertEqual(await running.last_assistant_text(), "final result")

        result = await self.supervisor.stop(spec.task_id, StopIntent.COMPLETE)
        self.assertTrue(result.reaped)
        self.assertIsNone(await self.supervisor.get(spec.task_id))

    async def test_startup_timeout_and_prompt_rejection_are_reaped(self):
        for scenario, code in (
            ("startup_hang", "startup_timeout"),
            ("prompt_reject", "prompt_rejected"),
        ):
            with self.subTest(scenario=scenario):
                spec = self.spec()
                with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": scenario}):
                    with self.assertRaises(PiProcessStartError) as raised:
                        await self.supervisor.start_task(spec)
                self.assertEqual(raised.exception.code.value, code)
                self.assertIsNone(await self.supervisor.get(spec.task_id))

    async def test_rpc_deadline_during_readiness_is_still_startup_timeout(self):
        settings = replace(
            self.settings,
            rpc_timeout_seconds=0.05,
            startup_timeout_seconds=0.2,
        )
        supervisor = PiProcessSupervisor(settings)
        spec = self.spec()
        try:
            with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "startup_hang"}):
                with self.assertRaises(PiProcessStartError) as raised:
                    await supervisor.start_task(spec)
            self.assertEqual(raised.exception.code, TaskErrorCode.STARTUP_TIMEOUT)
            self.assertIsNone(await supervisor.get(spec.task_id))
        finally:
            await supervisor.shutdown(1)

    async def test_protocol_error_and_nonzero_exit_are_observable(self):
        spec = self.spec()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "malformed_json"}):
            running = await self.supervisor.start_task(spec)
        with self.assertRaises(PiProtocolError):
            await anext(running.rpc.events())
        result = await self.supervisor.stop(spec.task_id, StopIntent.CANCEL)
        self.assertTrue(result.reaped)

        spec = self.spec()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "exit_nonzero"}):
            with self.assertRaises(PiProcessStartError) as raised:
                await self.supervisor.start_task(spec)
        self.assertEqual(raised.exception.code.value, "unexpected_exit")

    async def test_stderr_flood_is_drained_without_blocking_completion(self):
        spec = self.spec()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "stderr_flood"}):
            running = await self.supervisor.start_task(spec)
        iterator = running.rpc.events()
        await anext(iterator)
        self.assertEqual((await anext(iterator)).type, "agent_settled")
        self.assertLessEqual(len(running.rpc.stderr_tail), 128)
        await self.supervisor.stop(spec.task_id, StopIntent.COMPLETE)

    async def test_uncooperative_process_is_force_killed_and_reaped(self):
        spec = self.spec()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ignore_terminate"}):
            running = await self.supervisor.start_task(spec)
        result = await self.supervisor.stop(spec.task_id, StopIntent.CANCEL)

        self.assertTrue(result.reaped)
        self.assertTrue(result.forced)
        self.assertIsNotNone(running.process.returncode)

    async def test_shutdown_reaps_all_running_processes(self):
        specs = [self.spec(), self.spec()]
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ignore_abort"}):
            for spec in specs:
                await self.supervisor.start_task(spec)

        await self.supervisor.shutdown(1)

        for spec in specs:
            self.assertIsNone(await self.supervisor.get(spec.task_id))


if __name__ == "__main__":
    unittest.main()
