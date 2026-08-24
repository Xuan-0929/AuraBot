"""验证 execution.run_task 的单 Task happy path 与持久化结果投影。

测试使用真实 Store、Engine 协调能力和 fake Pi process，但直接调用 execution 主协程，
从而确认 queued→completed、输出事件与进程回收，不依赖 Manager 或 HTTP 层。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.task.models import CreateTaskCommand, TaskStatus
from backend.task.persistence.store import TaskStore
from backend.task.runtime.engine import TaskRuntimeEngine
from backend.task.runtime.execution import run_task
from backend.task.runtime.process import PiProcessSupervisor
from backend.task.runtime.scheduler import FIFOScheduler
from backend.task.settings import TaskSettings


class TaskExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_task_completes_and_reaps_fake_pi(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            project = root / "project"
            project.mkdir()
            plugin = root / "plugin.ts"
            prompt = root / "prompt.md"
            plugin.write_text("// fake\n", encoding="utf-8")
            prompt.write_text("prompt\n", encoding="utf-8")
            settings = TaskSettings(
                user_data_dir=root / "data",
                database_path=root / "data/task/task-runtime.sqlite3",
                session_root=root / "data/task/sessions",
                pi_home=root / "data/pi-agent",
                pi_runtime_dir=root,
                node_executable=sys.executable,
                pi_cli_path=Path(__file__).resolve().parents[2]
                / "fixtures/fake_pi_rpc.py",
                plugin_entry_path=plugin,
                system_prompt_path=prompt,
                concurrency_limit=1,
                default_timeout_seconds=10,
                startup_timeout_seconds=0.3,
                rpc_timeout_seconds=0.3,
                abort_grace_seconds=0.1,
                terminate_grace_seconds=0.1,
                shutdown_timeout_seconds=2,
                waiting_process_grace_seconds=0.1,
                max_jsonl_line_bytes=1024,
                protocol_queue_size=32,
                stderr_tail_bytes=128,
                output_chunk_bytes=16,
            )
            store = TaskStore(settings.database_path)
            scheduler = FIFOScheduler(1)
            supervisor = PiProcessSupervisor(settings)
            runtime = TaskRuntimeEngine(settings, store, scheduler, supervisor)
            await store.initialize()
            try:
                task = await store.create_task(
                    CreateTaskCommand(
                        title="Execution",
                        prompt="Complete",
                        working_directory=project,
                        timeout_seconds=10,
                    )
                )
                with patch.dict(
                    os.environ, {"AURABOT_FAKE_PI_SCENARIO": "complete"}
                ):
                    await run_task(
                        task.id,
                        runtime=runtime,
                        store=store,
                        supervisor=supervisor,
                        settings=settings,
                    )
                completed = await store.get_task(task.id)
                events = await store.events_after(0, task.id, 20)
            finally:
                await supervisor.shutdown(1)
                await scheduler.shutdown()
                await store.close()

        self.assertEqual(completed.status, TaskStatus.COMPLETED)
        self.assertEqual(completed.result_text, "final result")
        self.assertIn("task.output_appended", [event.type for event in events])
        self.assertIsNone(await supervisor.get(task.id))


if __name__ == "__main__":
    unittest.main()
