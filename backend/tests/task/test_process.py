"""验证最小 Pi process 函数的启动、错误映射、回收和 session 清理。

测试运行独立 fake Pi RPC 子进程，覆盖 pinned 命令、stderr DEVNULL、abort/terminate/kill
以及 symlink 不跟随；Pi 的 cwd 由 Task 已保存的受信任 workspace 解析结果提供，Manager
registry 和状态语义不属于本模块。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from dataclasses import fields, replace
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.task.models import CreateTaskCommand, TaskErrorCode
from backend.task.process import (
    PiProcess,
    PiProcessStartError,
    remove_session,
    start_pi,
    stop_pi,
)
from backend.task.settings import TaskSettings
from backend.task.store import TaskStore
from backend.main_agent.settings import WorkspaceRegistry


class PiProcessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.workspaces = WorkspaceRegistry({"project": self.project})
        self.fake_pi = Path(__file__).parent.parent / "fixtures/fake_pi_rpc.py"
        plugin, prompt = self.root / "plugin.ts", self.root / "prompt.md"
        plugin.write_text("// fake\n", encoding="utf-8")
        prompt.write_text("prompt\n", encoding="utf-8")
        self.settings = TaskSettings(
            database_path=self.root / "data/task/task-runtime.sqlite3",
            session_root=self.root / "data/task/sessions",
            pi_home=self.root / "data/pi-agent",
            node_executable=sys.executable,
            pi_cli_path=self.fake_pi,
            plugin_entry_path=plugin,
            system_prompt_path=prompt,
            default_timeout_seconds=30,
            startup_timeout_seconds=0.2,
            rpc_timeout_seconds=0.2,
            abort_grace_seconds=0.1,
            terminate_grace_seconds=0.1,
            shutdown_timeout_seconds=1,
            max_jsonl_line_bytes=1024,
            protocol_queue_size=16,
            output_chunk_bytes=16,
            workspace_registry=self.workspaces,
        )
        self.store = TaskStore(self.settings.database_path)
        self.store.initialize()
        self.processes: list[PiProcess] = []

    async def asyncTearDown(self):
        for pi in self.processes:
            await stop_pi(pi)
        self.store.close()
        self.temp.cleanup()

    def task(self, title: str = "Test Task"):
        return self.store.create_task(
            CreateTaskCommand(title, "Do the work", "project", "profile-1", str(uuid4()), 30),
            self.project,
        )

    async def start(self, scenario: str, prompt: str = "Do the work") -> PiProcess:
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": scenario}):
            pi = await start_pi(self.settings, self.task(), prompt)
        self.processes.append(pi)
        return pi

    async def test_start_uses_isolated_runtime_and_exposes_only_process_and_rpc(self):
        pi = await self.start("complete")
        self.assertEqual({field.name for field in fields(PiProcess)}, {"process", "rpc"})

        state = (await pi.rpc.command("get_state"))["data"]
        self.assertEqual(Path(state["cwd"]), self.project)
        self.assertEqual(Path(state["piHome"]), self.settings.pi_home)
        self.assertEqual(state["telemetry"], "0")
        self.assertEqual(state["versionCheck"], "1")
        self.assertIn(str(self.settings.plugin_entry_path), state["argv"])
        events = pi.rpc.events()
        self.assertEqual((await anext(events)).payload["text"], "done")
        self.assertEqual((await anext(events)).type, "agent_settled")
        self.assertEqual(await pi.last_assistant_text(), "final result")

        await stop_pi(pi, abort=False)
        self.assertIsNotNone(pi.process.returncode)

    async def test_startup_timeout_and_prompt_rejection_are_stable(self):
        for scenario, code in (
            ("startup_hang", TaskErrorCode.STARTUP_TIMEOUT),
            ("prompt_reject", TaskErrorCode.PROMPT_REJECTED),
            ("exit_nonzero", TaskErrorCode.UNEXPECTED_EXIT),
        ):
            with self.subTest(scenario=scenario):
                with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": scenario}):
                    with self.assertRaises(PiProcessStartError) as raised:
                        await start_pi(self.settings, self.task(scenario), "work")
                self.assertEqual(raised.exception.code, code)

        missing = replace(self.settings, node_executable=str(self.root / "missing-node"))
        with self.assertRaises(PiProcessStartError) as raised:
            await start_pi(missing, self.task("spawn"), "work")
        self.assertEqual(raised.exception.code, TaskErrorCode.SPAWN_FAILED)

    async def test_rpc_deadline_during_readiness_is_startup_timeout(self):
        settings = replace(self.settings, rpc_timeout_seconds=0.05)
        with patch.dict(os.environ, {"AURABOT_FAKE_PI_SCENARIO": "startup_hang"}):
            with self.assertRaises(PiProcessStartError) as raised:
                await start_pi(settings, self.task(), "work")
        self.assertEqual(raised.exception.code, TaskErrorCode.STARTUP_TIMEOUT)

    async def test_stderr_flood_uses_devnull_and_does_not_block(self):
        pi = await self.start("stderr_flood")
        events = pi.rpc.events()
        self.assertEqual((await anext(events)).type, "text_output")
        self.assertEqual((await anext(events)).type, "agent_settled")
        self.assertNotIn("stderr", {field.name for field in fields(PiProcess)})

    async def test_uncooperative_process_is_killed_and_reaped(self):
        pi = await self.start("ignore_terminate")
        await stop_pi(pi)
        self.assertIsNotNone(pi.process.returncode)

    async def test_remove_session_unlinks_symlink_without_following(self):
        outside = self.root / "outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        task_id = uuid4()
        session = self.settings.session_directory(task_id)
        session.parent.mkdir(parents=True)
        session.symlink_to(outside, target_is_directory=True)

        remove_session(self.settings, task_id)

        self.assertFalse(session.exists())
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
