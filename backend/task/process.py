"""启动、停止并完整回收一 Task 一 Pi RPC subprocess。

Manager 直接持有本模块返回的 PiProcess；``start_pi`` 构造隔离 session 和 pinned CLI，
``stop_pi`` 执行 abort→stdin close→wait→terminate→kill→reap，``remove_session`` 处理
tombstone 后清理。RPC 只管理 stdin/stdout，stderr 由操作系统 DEVNULL 消费。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from .models import TaskErrorCode, TaskRecord
from .rpc import PiCommandError, PiCommandTimeout, PiProtocolError, PiRpcClient
from .settings import TaskSettings


class PiProcessStartError(RuntimeError):
    """映射到稳定 Task error code 的 Pi 启动失败。"""

    def __init__(self, code: TaskErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


class TaskSessionCleanupError(RuntimeError):
    """tombstone 已提交，但 session 尚未安全删除。"""


@dataclass(slots=True)
class PiProcess:
    """Manager 持有的最小 live Pi handle。"""

    process: asyncio.subprocess.Process
    rpc: PiRpcClient

    async def last_assistant_text(self) -> str | None:
        response = await self.rpc.command("get_last_assistant_text")
        data = response.get("data")
        if not isinstance(data, Mapping):
            return None
        text = data.get("text")
        return text if isinstance(text, str) else None


async def start_pi(
    settings: TaskSettings, task: TaskRecord, prompt: str
) -> PiProcess:
    """Spawn Pi、验证 get_state readiness，并要求 prompt accepted。"""
    settings.session_directory(task.id).mkdir(parents=True, exist_ok=True)
    settings.pi_home.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "PI_CODING_AGENT_DIR": str(settings.pi_home),
            "PI_SKIP_VERSION_CHECK": "1",
            "PI_TELEMETRY": "0",
        }
    )
    command = [
        settings.node_executable,
        str(settings.pi_cli_path),
        "--mode",
        "rpc",
        "--session-dir",
        str(settings.session_directory(task.id)),
        "--session-id",
        str(task.id),
        "--name",
        task.title,
        "--no-approve",
        "--extension",
        str(settings.plugin_entry_path),
        "--append-system-prompt",
        str(settings.system_prompt_path),
    ]
    if settings.model_name:
        command.extend(["--provider", "aurabot-task", "--model", settings.model_name])
    platform_options: dict[str, Any] = {}
    if os.name == "nt":
        platform_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        platform_options["start_new_session"] = True
    spawn = asyncio.create_task(
        asyncio.create_subprocess_exec(
            *command,
            cwd=task.working_directory,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=settings.max_jsonl_line_bytes + 1,
            **platform_options,
        ),
        name=f"pi-spawn-{task.id}",
    )
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # create_subprocess_exec can be cancelled after the OS process exists but before
        # its handle reaches us. Finish that tiny boundary, then reap the raw process.
        with contextlib.suppress(OSError):
            process = await spawn
            await _stop_uninitialized(process, settings)
        raise
    except OSError as error:
        raise PiProcessStartError(
            TaskErrorCode.SPAWN_FAILED, "Failed to start the pinned Pi process."
        ) from error
    assert process.stdin is not None and process.stdout is not None
    rpc = PiRpcClient(
        stdin=process.stdin,
        stdout=process.stdout,
        response_timeout_seconds=settings.rpc_timeout_seconds,
        max_line_bytes=settings.max_jsonl_line_bytes,
        event_queue_size=settings.protocol_queue_size,
        output_chunk_bytes=settings.output_chunk_bytes,
    )
    await rpc.start()
    pi = PiProcess(process, rpc)
    try:
        try:
            await asyncio.wait_for(
                rpc.command("get_state"), timeout=settings.startup_timeout_seconds
            )
        except (TimeoutError, PiCommandTimeout) as error:
            raise PiProcessStartError(
                TaskErrorCode.STARTUP_TIMEOUT,
                "Pinned Pi did not become ready before the startup deadline.",
            ) from error
        try:
            await rpc.command("prompt", {"message": prompt})
        except PiCommandError as error:
            raise PiProcessStartError(
                TaskErrorCode.PROMPT_REJECTED, "Pi rejected the Task prompt."
            ) from error
        except PiProtocolError as error:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=0.05)
            if process.returncode is not None:
                raise PiProcessStartError(
                    TaskErrorCode.UNEXPECTED_EXIT,
                    "Pi exited before accepting the Task prompt.",
                ) from error
            raise PiProcessStartError(
                TaskErrorCode.PROTOCOL_ERROR,
                "Pi startup violated the RPC protocol.",
            ) from error
        return pi
    except BaseException:
        await stop_pi(pi)
        raise


async def stop_pi(pi: PiProcess, *, abort: bool = True) -> None:
    """按固定升级顺序停止 Pi，并且只在 subprocess 已 reap 后返回。"""
    process = pi.process
    if abort and process.returncode is None:
        with contextlib.suppress(BaseException):
            async with asyncio.timeout(pi.rpc.command_timeout_seconds):
                await pi.rpc.command("abort")
    with contextlib.suppress(BaseException):
        await pi.rpc.close_stdin()
    if process.returncode is None:
        try:
            await asyncio.wait_for(
                process.wait(), timeout=pi.rpc.command_timeout_seconds
            )
        except TimeoutError:
            _terminate_process_tree(process)
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=pi.rpc.command_timeout_seconds
                )
            except TimeoutError:
                _kill_process_tree(process)
                await process.wait()
    else:
        await process.wait()
    await pi.rpc.aclose()


async def _stop_uninitialized(
    process: asyncio.subprocess.Process, settings: TaskSettings
) -> None:
    """Reap a process cancelled before PiRpcClient ownership was established."""
    if process.stdin is not None:
        process.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await process.stdin.wait_closed()
    try:
        await asyncio.wait_for(process.wait(), timeout=settings.abort_grace_seconds)
    except TimeoutError:
        _terminate_process_tree(process)
        try:
            await asyncio.wait_for(
                process.wait(), timeout=settings.terminate_grace_seconds
            )
        except TimeoutError:
            _kill_process_tree(process)
            await process.wait()


def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name == "nt":
            process.terminate()
        else:
            os.killpg(process.pid, signal.SIGTERM)


def _kill_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)


def remove_session(settings: TaskSettings, task_id: UUID) -> None:
    """删除一个规范 Task session；路径本身是 symlink 时只 unlink。"""
    path = settings.session_directory(task_id)
    try:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError as error:
        raise TaskSessionCleanupError(
            f"Failed to remove Task session: {task_id}"
        ) from error


__all__ = [
    "PiProcess",
    "PiProcessStartError",
    "TaskSessionCleanupError",
    "remove_session",
    "start_pi",
    "stop_pi",
]
