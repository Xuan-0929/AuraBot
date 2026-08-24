"""
启动、停止并回收一 Task 一 Pi RPC subprocess。

PiProcessSupervisor 是进程句柄的唯一 owner：它用 TaskSettings 构造绝对 CLI 命令和
隔离环境，把字节流交给 PiRpcClient，并按 abort→stdin close→terminate→kill→wait
收口进程树。TaskRuntimeEngine 与 execution 主协程只通过 Supervisor 操作进程，不直接
持有 asyncio subprocess。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping
from uuid import UUID

from ..models import TaskErrorCode
from ..settings import TaskSettings
from .rpc import PiCommandError, PiCommandTimeout, PiProtocolError, PiRpcClient


class StopIntent(StrEnum):
    """Why the Control Plane is retiring a live Pi process."""

    COMPLETE = "complete"
    PAUSE = "pause"
    CANCEL = "cancel"
    WAITING = "waiting"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class TaskLaunchSpec:
    """Product-neutral values needed to start one Pi session process."""

    task_id: UUID
    title: str
    prompt: str
    working_directory: Path
    continuation_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class StopResult:
    """Observable result proving that a process handle was reaped."""

    returncode: int | None
    reaped: bool
    forced: bool


class PiProcessStartError(RuntimeError):
    """Stable startup failure mapped to a public Task error category."""

    def __init__(self, code: TaskErrorCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class RunningPiProcess:
    """Internal live handle; only PiProcessSupervisor creates and stores it."""

    task_id: UUID
    process: asyncio.subprocess.Process
    rpc: PiRpcClient
    ready_state: Mapping[str, Any]

    async def last_assistant_text(self) -> str | None:
        """Read the authoritative final assistant text after agent_settled."""

        response = await self.rpc.command("get_last_assistant_text")
        data = response.get("data")
        if not isinstance(data, Mapping):
            return None
        text = data.get("text")
        return text if isinstance(text, str) else None


class PiProcessSupervisor:
    """Own every live Pi handle and prevent duplicate processes for one Task."""

    def __init__(self, settings: TaskSettings) -> None:
        self._settings = settings
        self._running: dict[UUID, RunningPiProcess] = {}
        self._lock = asyncio.Lock()
        self._shutting_down = False

    async def start_task(self, spec: TaskLaunchSpec) -> RunningPiProcess:
        """Spawn Pi, prove readiness with get_state, then require prompt acceptance."""

        async with self._lock:
            if self._shutting_down:
                raise PiProcessStartError(
                    TaskErrorCode.INTERNAL_ERROR, "Task runtime is shutting down."
                )
            if spec.task_id in self._running:
                raise PiProcessStartError(
                    TaskErrorCode.INTERNAL_ERROR,
                    "A Pi process already exists for this Task.",
                )

            session_directory = self._settings.session_directory(spec.task_id)
            session_directory.mkdir(parents=True, exist_ok=True)
            self._settings.pi_home.mkdir(parents=True, exist_ok=True)
            environment = os.environ.copy()
            environment.update(
                {
                    "PI_CODING_AGENT_DIR": str(self._settings.pi_home),
                    "PI_SKIP_VERSION_CHECK": "1",
                    "PI_TELEMETRY": "0",
                }
            )
            command = [
                self._settings.node_executable,
                str(self._settings.pi_cli_path),
                "--mode",
                "rpc",
                "--session-dir",
                str(session_directory),
                "--session-id",
                str(spec.task_id),
                "--name",
                spec.title,
                "--no-approve",
                "--extension",
                str(self._settings.plugin_entry_path),
                "--append-system-prompt",
                str(self._settings.system_prompt_path),
            ]
            platform_options: dict[str, Any] = {}
            if os.name == "nt":
                platform_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                platform_options["start_new_session"] = True
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=spec.working_directory,
                    env=environment,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    limit=self._settings.max_jsonl_line_bytes + 1,
                    **platform_options,
                )
            except OSError as exc:
                raise PiProcessStartError(
                    TaskErrorCode.SPAWN_FAILED, "Failed to start the pinned Pi process."
                ) from exc
            assert process.stdin is not None
            assert process.stdout is not None
            assert process.stderr is not None
            rpc = PiRpcClient(
                stdin=process.stdin,
                stdout=process.stdout,
                stderr=process.stderr,
                response_timeout_seconds=self._settings.rpc_timeout_seconds,
                max_line_bytes=self._settings.max_jsonl_line_bytes,
                event_queue_size=self._settings.protocol_queue_size,
                stderr_tail_bytes=self._settings.stderr_tail_bytes,
                output_chunk_bytes=self._settings.output_chunk_bytes,
            )
            await rpc.start()
            running = RunningPiProcess(spec.task_id, process, rpc, {})
            self._running[spec.task_id] = running

        try:
            try:
                state = await asyncio.wait_for(
                    rpc.command("get_state"),
                    timeout=self._settings.startup_timeout_seconds,
                )
            except (TimeoutError, PiCommandTimeout) as exc:
                raise PiProcessStartError(
                    TaskErrorCode.STARTUP_TIMEOUT,
                    "Pinned Pi did not become ready before the startup deadline.",
                ) from exc
            running.ready_state = state
            prompt = spec.continuation_prompt or spec.prompt
            try:
                await rpc.command("prompt", {"message": prompt})
            except PiCommandError as exc:
                raise PiProcessStartError(
                    TaskErrorCode.PROMPT_REJECTED, "Pi rejected the Task prompt."
                ) from exc
            except PiProtocolError as exc:
                await self._map_early_exit(running, exc)
            return running
        except PiProcessStartError:
            await self.stop(spec.task_id, StopIntent.CANCEL)
            raise
        except PiProtocolError as exc:
            await self.stop(spec.task_id, StopIntent.CANCEL)
            raise PiProcessStartError(
                TaskErrorCode.PROTOCOL_ERROR, "Pi startup violated the RPC protocol."
            ) from exc
        except BaseException:
            await self.stop(spec.task_id, StopIntent.CANCEL)
            raise

    async def _map_early_exit(
        self, running: RunningPiProcess, cause: BaseException
    ) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(running.process.wait(), timeout=0.05)
        if running.process.returncode is not None:
            raise PiProcessStartError(
                TaskErrorCode.UNEXPECTED_EXIT,
                "Pi exited before accepting the Task prompt.",
            ) from cause
        raise PiProcessStartError(
            TaskErrorCode.PROTOCOL_ERROR, "Pi startup violated the RPC protocol."
        ) from cause

    async def get(self, task_id: UUID) -> RunningPiProcess | None:
        """Return the live internal handle for orchestration, if any."""

        async with self._lock:
            return self._running.get(task_id)

    async def stop(self, task_id: UUID, intent: StopIntent) -> StopResult:
        """Retire and reap one Task process through the approved escalation order."""

        async with self._lock:
            running = self._running.get(task_id)
        if running is None:
            return StopResult(returncode=None, reaped=True, forced=False)

        process = running.process
        forced = False
        if process.returncode is None and intent != StopIntent.COMPLETE:
            with contextlib.suppress(BaseException):
                async with asyncio.timeout(self._settings.abort_grace_seconds):
                    await running.rpc.command("abort")
        with contextlib.suppress(BaseException):
            await running.rpc.close_stdin()
        if process.returncode is None:
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=self._settings.abort_grace_seconds
                )
            except TimeoutError:
                forced = True
                self._terminate_process_tree(process)
                try:
                    await asyncio.wait_for(
                        process.wait(), timeout=self._settings.terminate_grace_seconds
                    )
                except TimeoutError:
                    self._kill_process_tree(process)
                    await process.wait()
        else:
            await process.wait()
        await running.rpc.aclose()
        async with self._lock:
            if self._running.get(task_id) is running:
                del self._running[task_id]
        return StopResult(
            returncode=process.returncode,
            reaped=process.returncode is not None,
            forced=forced,
        )

    @staticmethod
    def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)

    @staticmethod
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

    async def shutdown(self, deadline_seconds: float) -> None:
        """Stop admission and reap all owned workers concurrently within one deadline."""

        async with self._lock:
            self._shutting_down = True
            task_ids = tuple(self._running)
        if not task_ids:
            return
        async with asyncio.timeout(deadline_seconds):
            await asyncio.gather(
                *(self.stop(task_id, StopIntent.SHUTDOWN) for task_id in task_ids)
            )


__all__ = [
    "PiProcessStartError",
    "PiProcessSupervisor",
    "RunningPiProcess",
    "StopIntent",
    "StopResult",
    "TaskLaunchSpec",
]
