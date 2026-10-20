"""Regression checks for Windows workspace safety, optional Bash and graceful desktop shutdown.

Portable checks run on every host. Native handle/junction checks run only on Windows and exercise
the real WorkspaceReader; shutdown uses a real Uvicorn lifespan and an OS pipe without models.
Rename checks exercise both directory and ancestor locks, then verify release independently of
junction cleanup so a lock regression reports its original assertion.
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import uvicorn
from fastapi import FastAPI

from backend.desktop_runner import serve
from backend.main_agent.observations import WorkspaceReader, ObservationSecurityError, _relative_parts
from backend.main_agent.settings import WorkspaceRegistry, WorkspaceRegistryError
from backend.main_agent.tool_runtime import ToolSurfacePolicy
from backend.main_agent.windows_workspace import open_path, valid_component
from backend.task.settings import TaskSettings, find_windows_bash


class WindowsCompatibilityTests(unittest.TestCase):
    def test_missing_bash_does_not_fail_backend_composition(self):
        os.environ.setdefault("AURABOT_CONFIG_PATH", str(Path(__file__).parent / "fixtures/runtime_config.json"))
        from backend.main import build_task_manager
        from unittest.mock import Mock
        settings = Mock(shell_path=None)
        config = SimpleNamespace(path=Path("settings.json"), get=lambda *args: {"model": {"base_url": "http://localhost", "model_name": "test", "api_key": "key"}})
        with patch("backend.main.sys", SimpleNamespace(platform="win32")), \
             patch("backend.main.TaskSettings.from_config", return_value=settings), \
             patch("backend.main.TaskManager", return_value=SimpleNamespace()) as manager_type, \
             patch("backend.main.config", config):
            manager = build_task_manager()
            self.assertIs(manager, manager_type.return_value)
            self.assertFalse(manager.capability_available)
            self.assertEqual(manager.runtime_notice, "git_bash_missing")
            settings.validate_runtime.assert_not_called()

    def test_aliases_devices_and_streams_are_rejected(self):
        for name in ("C:outside", "safe.txt:secret", ".env.", ".env ", "CON", "NUL.txt", "COM¹", "..", "a\\b"):
            self.assertFalse(valid_component(name), name)
        self.assertTrue(valid_component("中文 file.txt"))
        with patch("backend.main_agent.observations.sys", SimpleNamespace(platform="win32")):
            for name in ("safe.txt:secret", ".env.", "NUL.txt", "C:/outside"):
                with self.assertRaises(ObservationSecurityError):
                    _relative_parts(name, allow_root=False)

    def test_unavailable_tasks_keep_workspace_reads_and_cleanup(self):
        policy = ToolSurfacePolicy(workspace_ids=("repo",), tasks_available=False,
                                   task_controls=("inspect_tasks", "resume_task", "rerun_task", "answer_task_question", "cancel_task", "delete_task"))
        names = {d.name for d in policy.definitions}
        self.assertTrue({"workspace_list", "workspace_read", "inspect_tasks", "cancel_task", "delete_task"} <= names)
        self.assertFalse({"task_assign", "resume_task", "rerun_task", "answer_task_question"} & names)

    def test_optional_bash_is_verified_and_pinned_in_pi_settings(self):
        with tempfile.TemporaryDirectory(prefix="aura shell 中文 ") as directory:
            root = Path(directory)
            bash = root / "Programs/Git/bin/bash.exe"
            bash.parent.mkdir(parents=True)
            bash.touch()
            with patch.dict(os.environ, {"LOCALAPPDATA": str(root)}, clear=True), patch("shutil.which", return_value=None):
                with patch("backend.task.settings.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout=b"GNU bash, version 5.2")) as run:
                    self.assertEqual(find_windows_bash(), str(bash.resolve()))
                    self.assertEqual(run.call_args.args[0], [str(bash), "--version"])
                with patch("backend.task.settings.subprocess.run", side_effect=subprocess.TimeoutExpired("bash", 3)):
                    self.assertIsNone(find_windows_bash())
                bash.unlink()
                self.assertIsNone(find_windows_bash())
            settings = TaskSettings(root / "db", root / "sessions", root / "pi", "node", root / "cli", root / "plugin", root / "prompt", 30, shell_path=str(bash))
            settings.pi_home.mkdir()
            config = settings.pi_home / "settings.json"
            config.write_text(json.dumps({"defaultModel": "test"}), encoding="utf-8")
            settings.configure_shell()
            self.assertEqual(json.loads(config.read_text(encoding="utf-8")), {"defaultModel": "test", "shellPath": str(bash)})

    @unittest.skipUnless(sys.platform == "win32", "requires real Windows handles")
    def test_native_reads_and_junction_rejection(self):
        with tempfile.TemporaryDirectory(prefix="aura workspace 中文 ") as directory:
            root = Path(directory).resolve()
            workspace, outside = root / "workspace", root / "outside"
            workspace.mkdir(); outside.mkdir()
            (workspace / "safe.txt").write_bytes("一\r\n二\r\n".encode())
            (workspace / ".env").write_text("hidden", encoding="utf-8")
            secret = workspace / "private-key-long-name.txt"
            secret.write_text("hidden", encoding="utf-8")
            (outside / "private.txt").write_text("outside", encoding="utf-8")
            junction = workspace / "junction"
            # mklink is a cmd built-in; list arguments are only temporary paths generated here.
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                reader = WorkspaceReader(WorkspaceRegistry({"repo": workspace}))
                self.assertEqual([item["name"] for item in reader.list("repo")["items"]], ["safe.txt"])
                self.assertEqual(reader.read("repo", "safe.txt", start_line=2)["content"], "二\r\n")
                with self.assertRaises(ObservationSecurityError):
                    reader.read("repo", "junction/private.txt")
                with self.assertRaises(WorkspaceRegistryError):
                    WorkspaceRegistry({"bad": junction})
                with self.assertRaises(ObservationSecurityError):
                    reader.read("repo", "safe.txt:stream")
                import ctypes
                from ctypes import wintypes
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
                kernel.GetShortPathNameW.restype = wintypes.DWORD
                buffer = ctypes.create_unicode_buffer(32768)
                self.assertTrue(kernel.GetShortPathNameW(str(secret), buffer, len(buffer)))
                alias = Path(buffer.value).name
                if alias.casefold() != secret.name.casefold():
                    with self.assertRaises(ObservationSecurityError):
                        reader.read("repo", alias)
            finally:
                junction.rmdir()

    @unittest.skipUnless(sys.platform == "win32", "requires real Windows handles")
    def test_native_directory_handles_block_rename_until_closed(self):
        with tempfile.TemporaryDirectory(prefix="aura locks 中文 ") as directory:
            root = Path(directory).resolve()
            workspace = root / "workspace"
            nested = workspace / "nested"
            nested.mkdir(parents=True)
            replacement = root / "replaced"
            for target in (workspace, nested):
                with self.subTest(target=target.name):
                    try:
                        with open_path(nested, directory=True):
                            with self.assertRaises(OSError):
                                target.rename(replacement)
                        target.rename(replacement)
                    finally:
                        if replacement.exists():
                            replacement.rename(target)


class DesktopShutdownTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(sys.platform == "win32", "requires real Windows process trees")
    async def test_forced_task_cleanup_includes_descendants(self):
        import ctypes
        from ctypes import wintypes
        from backend.task.process import _terminate_process_tree
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        parent = await asyncio.create_subprocess_exec(sys.executable, "-c",
            "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(child.pid,flush=True); time.sleep(60)",
            stdout=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
        handle = None
        try:
            child_pid = int(await asyncio.wait_for(parent.stdout.readline(), 5))
            handle = kernel.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
            self.assertTrue(handle)
            _terminate_process_tree(parent)
            await asyncio.wait_for(parent.wait(), 5)
            self.assertEqual(await asyncio.to_thread(kernel.WaitForSingleObject, handle, 5000), 0)
        finally:
            if parent.returncode is None:
                subprocess.run(["taskkill", "/PID", str(parent.pid), "/T", "/F"], capture_output=True, timeout=5)
                await parent.wait()
            if handle:
                kernel.CloseHandle(handle)

    async def test_parent_pipe_eof_runs_lifespan_cleanup_even_during_startup(self):
        for early in (False, True):
            with self.subTest(early=early):
                started, closed = asyncio.Event(), asyncio.Event()

                @asynccontextmanager
                async def lifespan(app):
                    started.set()
                    if early:
                        await asyncio.sleep(0.05)
                    try:
                        yield
                    finally:
                        closed.set()

                server = uvicorn.Server(uvicorn.Config(FastAPI(lifespan=lifespan), host="127.0.0.1", port=0, lifespan="on", log_level="critical"))
                read_fd, write_fd = os.pipe()
                with os.fdopen(read_fd) as control:
                    worker = asyncio.create_task(serve(server, control))
                    try:
                        await asyncio.wait_for(started.wait(), 3)
                        if not early:
                            while not server.started:
                                await asyncio.sleep(0.01)
                    finally:
                        os.close(write_fd)
                    await asyncio.wait_for(worker, 3)
                self.assertTrue(closed.is_set())
