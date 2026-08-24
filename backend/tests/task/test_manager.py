"""验证 TaskManager 是运行状态、FIFO、进程与 fail-stop 的唯一 owner。

测试使用真实同步 Store 和 fake Pi subprocess 覆盖固定五并发、立即 waiting 回收、命令
CAS、timeout、recovery 与并行 shutdown；Gateway 与 JSON DTO 在各自测试中验证。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.task.manager import MAX_ACTIVE_TASKS, TaskManager
from backend.task.models import (
    CreateTaskCommand,
    TaskErrorCode,
    TaskRuntimeUnavailable,
    TaskStatus,
)
from backend.task.settings import TaskSettings
from backend.task.store import TaskStore
from backend.task.process import TaskSessionCleanupError


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        fake_pi = Path(__file__).parent.parent / "fixtures/fake_pi_rpc.py"
        plugin, prompt = self.root / "plugin.ts", self.root / "prompt.md"
        plugin.write_text("// fake\n", encoding="utf-8")
        prompt.write_text("prompt\n", encoding="utf-8")
        self.settings = TaskSettings(
            database_path=self.root / "data/task/task-runtime.sqlite3",
            session_root=self.root / "data/task/sessions",
            pi_home=self.root / "data/pi-agent",
            node_executable=sys.executable,
            pi_cli_path=fake_pi,
            plugin_entry_path=plugin,
            system_prompt_path=prompt,
            default_timeout_seconds=10,
            startup_timeout_seconds=0.3,
            rpc_timeout_seconds=0.3,
            abort_grace_seconds=0.1,
            terminate_grace_seconds=0.1,
            shutdown_timeout_seconds=3,
            max_jsonl_line_bytes=1024,
            protocol_queue_size=32,
            output_chunk_bytes=16,
        )
        self.manager = TaskManager(self.settings)
        self.started = False

    async def asyncTearDown(self):
        if self.started:
            await self.manager.shutdown()
        self.temp.cleanup()

    async def startup(self):
        await self.manager.startup()
        self.started = True

    def command(self, index: int = 1, timeout: int | None = 10):
        return CreateTaskCommand(
            title=f"Task {index}", prompt=f"Prompt {index}",
            working_directory=self.project, timeout_seconds=timeout,
        )

    async def wait_status(self, task_id, status: TaskStatus, timeout: float = 4):
        async with asyncio.timeout(timeout):
            while True:
                task = await self.manager.get_task(task_id)
                if task.status == status:
                    return task
                await asyncio.sleep(0.01)

    async def wait_no_run(self, task_id, timeout: float = 3):
        async with asyncio.timeout(timeout):
            while task_id in self.manager._runs:
                await asyncio.sleep(0.01)

    async def wait_pi(self, task_id, timeout: float = 3):
        async with asyncio.timeout(timeout):
            while True:
                run = self.manager._runs.get(task_id)
                if run is not None and run.pi is not None:
                    return run.pi
                await asyncio.sleep(0.01)

    async def test_create_uses_default_and_completes_with_output(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}):
            created = await self.manager.create_task(self.command(timeout=None))
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)

        self.assertEqual(created.timeout_seconds, 10)
        self.assertEqual(completed.result_text, "final result")
        self.assertGreater(completed.active_runtime_ms, 0)
        events = self.manager._store.events_after(0, created.id, 100)
        self.assertIn("task.output_appended", [event.type for event in events])
        self.assertEqual(events[-1].payload["to"], "completed")
        self.assertNotIn(created.id, self.manager._runs)

    async def test_sqlite_fifo_admits_exactly_five_then_refills_one_slot(self):
        self.assertEqual(MAX_ACTIVE_TASKS, 5)
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            tasks = [await self.manager.create_task(self.command(i)) for i in range(6)]
            for task in tasks[:5]:
                await self.wait_status(task.id, TaskStatus.RUNNING)
            await asyncio.sleep(0.05)
            sixth = await self.manager.get_task(tasks[5].id)
            self.assertEqual(sixth.status, TaskStatus.QUEUED)
            self.assertEqual(len(self.manager._runs), 5)

            first = await self.manager.get_task(tasks[0].id)
            paused = await self.manager.pause_task(first.id, first.version)
            self.assertEqual(paused.status, TaskStatus.PAUSED)
            await self.wait_status(sixth.id, TaskStatus.RUNNING)
            self.assertEqual(len(self.manager._runs), 5)

            seventh = await self.manager.create_task(self.command(7))
            paused_queued = await self.manager.pause_task(seventh.id, seventh.version)
            self.assertEqual(paused_queued.status, TaskStatus.PAUSED)
            cancelled_paused = await self.manager.cancel_task(
                seventh.id, paused_queued.version
            )
            self.assertEqual(cancelled_paused.status, TaskStatus.CANCELLED)
            eighth = await self.manager.create_task(self.command(8))
            cancelled_queued = await self.manager.cancel_task(eighth.id, eighth.version)
            self.assertEqual(cancelled_queued.status, TaskStatus.CANCELLED)

    async def test_question_immediately_reaps_and_answer_always_requeues(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ask_main_agent"}):
            created = await self.manager.create_task(self.command())
            waiting = await self.wait_status(created.id, TaskStatus.WAITING)
            await self.wait_no_run(created.id)
            self.assertIsNotNone(waiting.question_id)

            os.environ["AURABOT_FAKE_PI_SCENARIO"] = "complete"
            os.environ["AURABOT_FAKE_EXPECT_PROMPT_CONTAINS"] = "blue"
            queued = await self.manager.answer_question(
                created.id, waiting.question_id, "blue", waiting.version
            )
            self.assertEqual(queued.status, TaskStatus.QUEUED)
            self.assertEqual(queued.question_answer, "blue")
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)

        self.assertEqual(completed.result_text, "final result")
        self.assertIsNone(completed.question_id)
        events = self.manager._store.events_after(0, created.id, 100)
        self.assertEqual([event.type for event in events].count("task.question_answered"), 1)

    async def test_waiting_pause_resume_and_answered_pause_resume(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ask_main_agent"}):
            task = await self.manager.create_task(self.command())
            waiting = await self.wait_status(task.id, TaskStatus.WAITING)
            paused = await self.manager.pause_task(task.id, waiting.version)
            restored = await self.manager.resume_task(task.id, paused.version)
            self.assertEqual(restored.status, TaskStatus.WAITING)

            queued = await self.manager.answer_question(
                task.id, restored.question_id, "blue", restored.version
            )
            paused_answer = await self.manager.pause_task(task.id, queued.version)
            resumed = await self.manager.resume_task(task.id, paused_answer.version)
            self.assertEqual(resumed.status, TaskStatus.QUEUED)

            another = await self.manager.create_task(self.command(2))
            waiting = await self.wait_status(another.id, TaskStatus.WAITING)
            cancelled = await self.manager.cancel_task(another.id, waiting.version)
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
            self.assertNotIn(another.id, self.manager._runs)

    async def test_running_pause_and_cancel_commit_only_after_reap(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            first = await self.manager.create_task(self.command(1))
            running = await self.wait_status(first.id, TaskStatus.RUNNING)
            process = (await self.wait_pi(first.id)).process
            paused = await self.manager.pause_task(first.id, running.version)
            self.assertIsNotNone(process.returncode)
            self.assertEqual(paused.status, TaskStatus.PAUSED)

            second = await self.manager.create_task(self.command(2))
            running = await self.wait_status(second.id, TaskStatus.RUNNING)
            process = (await self.wait_pi(second.id)).process
            cancelled = await self.manager.cancel_task(second.id, running.version)
            self.assertIsNotNone(process.returncode)
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)

    async def test_timeout_nonzero_exit_and_model_error_are_stable(self):
        await self.startup()
        cases = (
            ("running_hang", 1, TaskErrorCode.TIMEOUT),
            ("exit_nonzero_after_accept", 10, TaskErrorCode.UNEXPECTED_EXIT),
            ("model_error", 10, TaskErrorCode.MODEL_ERROR),
            ("malformed_json", 10, TaskErrorCode.PROTOCOL_ERROR),
            ("extension_error", 10, TaskErrorCode.RESOURCE_LOAD_FAILED),
            ("missing_result", 10, TaskErrorCode.RESULT_UNAVAILABLE),
        )
        for index, (scenario, timeout, code) in enumerate(cases, start=1):
            with self.subTest(scenario=scenario):
                with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": scenario}):
                    task = await self.manager.create_task(self.command(index, timeout))
                    failed = await self.wait_status(task.id, TaskStatus.FAILED)
                self.assertEqual(failed.error_code, code)
                self.assertNotIn("provider secret detail", failed.error_message or "")

    async def test_restart_repairs_running_and_preserves_other_durable_states(self):
        seed = TaskStore(self.settings.database_path)
        seed.initialize()
        resumable = seed.create_task(self.command(1))
        resumable = seed.claim_queued(1)[0]
        self.settings.session_directory(resumable.id).mkdir(parents=True)
        missing = seed.create_task(self.command(2))
        missing = seed.claim_queued(1)[0]
        waiting_source = seed.create_task(self.command(3))
        waiting_source = seed.claim_queued(1)[0]
        waiting, _ = seed.raise_question(
            waiting_source.id, "Choose", (), expected_version=waiting_source.version
        )
        seed.close()

        await self.startup()

        paused = await self.manager.get_task(resumable.id)
        failed = await self.manager.get_task(missing.id)
        self.assertEqual(paused.status, TaskStatus.PAUSED)
        self.assertEqual(paused.pause_reason, "backend_restarted")
        self.assertEqual(failed.error_code, TaskErrorCode.SESSION_MISSING)
        self.assertEqual((await self.manager.get_task(waiting.id)).status, TaskStatus.WAITING)

    async def test_shutdown_reaps_five_in_parallel_and_pauses_running(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ignore_abort"}):
            tasks = [await self.manager.create_task(self.command(i)) for i in range(5)]
            for task in tasks:
                await self.wait_status(task.id, TaskStatus.RUNNING)
            started = time.monotonic()
            await self.manager.shutdown()
            elapsed = time.monotonic() - started
            self.started = False

        reopened = TaskStore(self.settings.database_path)
        reopened.initialize()
        self.assertLess(elapsed, 1.5)
        for task in tasks:
            paused = reopened.get_task(task.id)
            self.assertEqual(paused.status, TaskStatus.PAUSED)
            self.assertEqual(paused.pause_reason, "backend_shutdown")
        reopened.close()

    async def test_durable_write_failure_stops_admission_and_reaps_all_pi(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            tasks = [await self.manager.create_task(self.command(i)) for i in range(2)]
            for task in tasks:
                await self.wait_status(task.id, TaskStatus.RUNNING)
            with sqlite3.connect(self.settings.database_path) as connection:
                connection.execute(
                    """CREATE TRIGGER reject_status_event BEFORE INSERT ON task_events
                       WHEN NEW.event_type='task.status_changed'
                       BEGIN SELECT RAISE(ABORT, 'durability lost'); END"""
                )
            current = await self.manager.get_task(tasks[0].id)
            with self.assertRaises(TaskRuntimeUnavailable):
                await self.manager.pause_task(current.id, current.version)
            async with asyncio.timeout(3):
                while self.manager._runs:
                    await asyncio.sleep(0.01)
            with self.assertRaises(TaskRuntimeUnavailable):
                await self.manager.create_task(self.command(3))

    async def test_delete_commits_tombstone_before_cleanup_and_retries_idempotently(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}):
            task = await self.manager.create_task(self.command())
            await self.wait_status(task.id, TaskStatus.COMPLETED)

        with patch(
            "backend.task.manager.remove_session",
            side_effect=TaskSessionCleanupError("cleanup failed"),
        ):
            with self.assertRaises(TaskSessionCleanupError):
                await self.manager.delete_task(task.id)

        deleted = await self.manager.get_task(task.id, include_deleted=True)
        self.assertIsNotNone(deleted.deleted_at)
        await self.manager.delete_task(task.id)
        events = self.manager._store.events_after(0, task.id, 100)
        self.assertEqual([event.type for event in events].count("task.deleted"), 1)


if __name__ == "__main__":
    unittest.main()
