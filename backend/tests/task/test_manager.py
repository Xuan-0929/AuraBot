"""
验证 TaskManager 在真实 Store、Scheduler 与 fake Pi Supervisor 上的完整业务编排。

测试覆盖后端垂直切片的状态持久化、FIFO、pause/resume/cancel、问题回答、active timeout、
restart reconciliation 与 shutdown；它不经过 FastAPI，也不依赖模型、网络或 Electron。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.task.manager import TaskManager, TaskRuntimeUnavailable
from backend.task.models import CreateTaskCommand, TaskErrorCode, TaskStatus
from backend.task.process import PiProcessSupervisor
from backend.task.scheduler import FifoScheduler
from backend.task.settings import TaskSettings
from backend.task.store import TaskStore


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        fake_pi = Path(__file__).resolve().parents[1] / "fixtures/fake_pi_rpc.py"
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
            pi_cli_path=fake_pi,
            plugin_entry_path=plugin,
            system_prompt_path=prompt,
            concurrency_limit=1,
            default_timeout_seconds=10,
            startup_timeout_seconds=0.3,
            rpc_timeout_seconds=0.3,
            abort_grace_seconds=0.1,
            terminate_grace_seconds=0.1,
            shutdown_timeout_seconds=2,
            waiting_process_grace_seconds=0.15,
            max_jsonl_line_bytes=1024,
            protocol_queue_size=32,
            stderr_tail_bytes=128,
            output_chunk_bytes=16,
        )
        self.store = TaskStore(self.settings.database_path, self.settings.session_root)
        self.scheduler = FifoScheduler(self.settings.concurrency_limit)
        self.supervisor = PiProcessSupervisor(self.settings)
        self.manager = TaskManager(
            self.settings, self.store, self.scheduler, self.supervisor
        )
        self.started = False

    async def asyncTearDown(self):
        if self.started:
            await self.manager.shutdown()
        else:
            await self.supervisor.shutdown(1)
            await self.store.close()
        self.temp.cleanup()

    async def startup(self):
        await self.manager.startup()
        self.started = True

    def command(self, index: int = 1, timeout: int = 10) -> CreateTaskCommand:
        return CreateTaskCommand(
            title=f"Task {index}",
            prompt=f"Prompt {index}",
            working_directory=self.project,
            timeout_seconds=timeout,
        )

    async def wait_status(self, task_id, status: TaskStatus, timeout: float = 3):
        async with asyncio.timeout(timeout):
            while True:
                task = await self.manager.get_task(task_id)
                if task.status == status:
                    return task
                await asyncio.sleep(0.01)

    async def test_create_runs_to_completion_and_persists_output(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}):
            created = await self.manager.create_task(self.command())
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)

        self.assertEqual(completed.result_text, "final result")
        self.assertGreater(completed.active_runtime_ms, 0)
        events = await self.store.events_after(0, created.id, 100)
        self.assertIn("task.output_appended", [event.type for event in events])
        self.assertEqual(events[-1].payload["to"], "completed")
        self.assertIsNone(await self.supervisor.get(created.id))

    async def test_fifo_keeps_second_queued_and_queued_pause_cancel_are_immediate(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ask_main_agent"}):
            first = await self.manager.create_task(self.command(1))
            await self.wait_status(first.id, TaskStatus.WAITING)
            second = await self.manager.create_task(self.command(2))
            await asyncio.sleep(0.05)
            self.assertEqual((await self.manager.get_task(second.id)).status, TaskStatus.QUEUED)

            paused = await self.manager.pause_task(second.id, second.version)
            self.assertEqual(paused.status, TaskStatus.PAUSED)
            cancelled = await self.manager.cancel_task(second.id, paused.version)
            self.assertEqual(cancelled.status, TaskStatus.CANCELLED)

            first_current = await self.manager.get_task(first.id)
            await self.manager.cancel_task(first.id, first_current.version)

    async def test_active_pause_reaps_and_resume_uses_new_process(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            created = await self.manager.create_task(self.command())
            running = await self.wait_status(created.id, TaskStatus.RUNNING)
            paused = await self.manager.pause_task(created.id, running.version)

        self.assertEqual(paused.status, TaskStatus.PAUSED)
        self.assertIsNone(await self.supervisor.get(created.id))
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}):
            resumed = await self.manager.resume_task(created.id, paused.version)
            self.assertEqual(resumed.status, TaskStatus.QUEUED)
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)
        self.assertEqual(completed.result_text, "final result")

    async def test_question_answer_resumes_live_rpc_and_completes(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ask_main_agent"}):
            created = await self.manager.create_task(self.command())
            waiting = await self.wait_status(created.id, TaskStatus.WAITING)
            question = await self.store.pending_question(created.id)
            self.assertIsNotNone(question)
            answered = await self.manager.answer_question(
                created.id,
                question.id,
                "blue",
                waiting.version,
            )
            self.assertEqual(answered.status, TaskStatus.RUNNING)
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)

        self.assertEqual(completed.result_text, "final result")
        self.assertIsNone(await self.store.pending_question(created.id))

    async def test_waiting_hibernates_then_answer_requeues_session(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "ask_main_agent"}):
            created = await self.manager.create_task(self.command())
            waiting = await self.wait_status(created.id, TaskStatus.WAITING)
            question = await self.store.pending_question(created.id)
            self.assertIsNotNone(question)
            async with asyncio.timeout(2):
                while await self.supervisor.get(created.id) is not None:
                    await asyncio.sleep(0.01)

            os.environ["AURABOT_FAKE_PI_SCENARIO"] = "complete"
            queued = await self.manager.answer_question(
                created.id, question.id, "blue", waiting.version
            )
            self.assertEqual(queued.status, TaskStatus.QUEUED)
            completed = await self.wait_status(created.id, TaskStatus.COMPLETED)

        self.assertEqual(completed.result_text, "final result")

    async def test_active_timeout_fails_without_task_level_retry(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            created = await self.manager.create_task(self.command(timeout=1))
            failed = await self.wait_status(created.id, TaskStatus.FAILED, timeout=3)

        self.assertEqual(failed.error_code, TaskErrorCode.TIMEOUT)
        self.assertIsNone(await self.supervisor.get(created.id))

    async def test_nonzero_exit_after_prompt_acceptance_is_unexpected_exit(self):
        await self.startup()
        with patch.dict(
            os.environ, {"AURABOT_FAKE_PI_SCENARIO": "exit_nonzero_after_accept"}
        ):
            created = await self.manager.create_task(self.command())
            failed = await self.wait_status(created.id, TaskStatus.FAILED)

        self.assertEqual(failed.error_code, TaskErrorCode.UNEXPECTED_EXIT)
        self.assertIsNone(await self.supervisor.get(created.id))

    async def test_agent_settled_after_model_error_does_not_complete_task(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "model_error"}):
            created = await self.manager.create_task(self.command())
            failed = await self.wait_status(created.id, TaskStatus.FAILED)

        self.assertEqual(failed.error_code, TaskErrorCode.MODEL_ERROR)
        self.assertNotIn("provider secret detail", failed.error_message)
        self.assertIsNone(await self.supervisor.get(created.id))

    async def test_restart_reconciliation_pauses_session_and_fails_missing_session(self):
        await self.store.initialize()
        resumable = await self.store.create_task(self.command(1))
        resumable, _ = await self.store.transition_task(
            resumable.id, TaskStatus.STARTING, expected_version=resumable.version
        )
        self.settings.session_directory(resumable.id).mkdir(parents=True)
        missing = await self.store.create_task(self.command(2))
        missing, _ = await self.store.transition_task(
            missing.id, TaskStatus.STARTING, expected_version=missing.version
        )

        await self.startup()

        paused = await self.manager.get_task(resumable.id)
        failed = await self.manager.get_task(missing.id)
        self.assertEqual(paused.status, TaskStatus.PAUSED)
        self.assertEqual(paused.pause_reason, "backend_restarted")
        self.assertEqual(failed.status, TaskStatus.FAILED)
        self.assertEqual(failed.error_code, TaskErrorCode.SESSION_MISSING)

    async def test_shutdown_pauses_active_task_after_reaping(self):
        await self.startup()
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "running_hang"}):
            created = await self.manager.create_task(self.command())
            await self.wait_status(created.id, TaskStatus.RUNNING)
            await self.manager.shutdown()
            self.started = False

        reopened = TaskStore(self.settings.database_path, self.settings.session_root)
        await reopened.initialize()
        paused = await reopened.get_task(created.id)
        await reopened.close()
        self.assertEqual(paused.status, TaskStatus.PAUSED)
        self.assertEqual(paused.pause_reason, "backend_shutdown")

    async def test_store_write_failure_stops_admission_and_reaps_workers(self):
        await self.store.initialize()
        created = await self.store.create_task(self.command())
        await self.store.close()
        with sqlite3.connect(self.settings.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_manager_status_event
                BEFORE INSERT ON task_events
                WHEN NEW.event_type = 'task.status_changed'
                BEGIN
                  SELECT RAISE(ABORT, 'durability lost');
                END
                """
            )

        await self.startup()
        async with asyncio.timeout(2):
            while self.manager._fatal_error is None:
                await asyncio.sleep(0.01)

        with self.assertRaises(TaskRuntimeUnavailable):
            await self.manager.create_task(self.command(2))
        self.assertIsNone(await self.supervisor.get(created.id))


if __name__ == "__main__":
    unittest.main()
