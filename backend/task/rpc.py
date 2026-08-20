"""
实现 AuraBot 所需的严格 Pi stdin/stdout JSONL 协议子集。

PiRpcClient 负责 LF framing、command id correlation、有界事件队列、text delta 合并和
stderr drain；它不启动/终止进程，也不理解 Task 产品状态。Supervisor 提供字节流，
TaskManager 消费归一化事件，任何 framing/correlation 破坏都会成为 fatal protocol error。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class _AsyncWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


class PiRpcError(RuntimeError):
    """Base class for RPC boundary failures."""


class PiProtocolError(PiRpcError):
    """Fatal malformed framing, payload or response correlation."""


class PiCommandError(PiRpcError):
    """A well-formed Pi response rejected one command."""


class PiCommandTimeout(PiCommandError):
    """Pi did not return a command response within the configured deadline."""


@dataclass(frozen=True, slots=True)
class PiEvent:
    """Minimal normalized event passed upward without Pi model/tool internals."""

    type: str
    payload: Mapping[str, Any]


_KNOWN_EVENT_TYPES = frozenset(
    {
        "agent_start",
        "agent_end",
        "agent_settled",
        "extension_ui_request",
        "extension_error",
        "auto_retry_start",
        "auto_retry_end",
    }
)


class PiRpcClient:
    """One strict JSONL client attached to one subprocess owned elsewhere."""

    def __init__(
        self,
        *,
        stdin: _AsyncWriter,
        stdout: asyncio.StreamReader,
        stderr: asyncio.StreamReader,
        response_timeout_seconds: float,
        max_line_bytes: int,
        event_queue_size: int,
        stderr_tail_bytes: int,
        output_chunk_bytes: int,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self._response_timeout = response_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._stderr_tail_bytes = stderr_tail_bytes
        self._output_chunk_bytes = output_chunk_bytes
        self._events: asyncio.Queue[PiEvent | PiProtocolError | None] = asyncio.Queue(
            maxsize=event_queue_size
        )
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._next_command_id = 1
        self._fatal_error: PiProtocolError | None = None
        self._text_buffer = ""
        self._stderr_tail = bytearray()
        self._closed = False

    @property
    def stderr_tail(self) -> bytes:
        """Return a bounded diagnostic tail for structured logging only."""

        return bytes(self._stderr_tail)

    async def start(self) -> None:
        """Start exactly one stdout parser and one stderr drainer."""

        if self._reader_task is not None:
            return
        self._reader_task = asyncio.create_task(
            self._read_stdout(), name="pi-rpc-stdout"
        )
        self._stderr_task = asyncio.create_task(
            self._drain_stderr(), name="pi-rpc-stderr"
        )

    async def command(
        self,
        command_type: str,
        payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        """Write one command and await only the response with its generated id."""

        self._raise_if_unavailable()
        command_id = f"cmd-{self._next_command_id}"
        self._next_command_id += 1
        future: asyncio.Future[Mapping[str, Any]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[command_id] = future
        message: dict[str, object] = {"id": command_id, "type": command_type}
        if payload:
            message.update(payload)
        try:
            await self._write_json(message)
            return await asyncio.wait_for(future, timeout=self._response_timeout)
        except TimeoutError as exc:
            self._pending.pop(command_id, None)
            raise PiCommandTimeout(
                f"Pi RPC command {command_type} timed out."
            ) from exc
        except BaseException:
            self._pending.pop(command_id, None)
            raise

    async def events(self) -> AsyncIterator[PiEvent]:
        """Yield normalized events until clean EOF or a fatal protocol error."""

        while True:
            item = await self._events.get()
            if item is None:
                return
            if isinstance(item, PiProtocolError):
                raise item
            yield item

    async def send_extension_ui_response(
        self, request_id: str, answer: object
    ) -> None:
        """Answer/cancel a Pi Extension dialog without creating a command future."""

        response: dict[str, object] = {
            "type": "extension_ui_response",
            "id": request_id,
        }
        if answer is None:
            response["cancelled"] = True
        elif isinstance(answer, bool):
            response["confirmed"] = answer
        else:
            response["value"] = str(answer)
        await self._write_json(response)

    async def close_stdin(self) -> None:
        """Close the child stdin once; Supervisor still owns process reaping."""

        if self._closed:
            return
        self._closed = True
        self._stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await self._stdin.wait_closed()

    async def aclose(self) -> None:
        """Stop parser tasks and close stdin without waiting on the subprocess."""

        await self.close_stdin()
        tasks = tuple(
            task for task in (self._reader_task, self._stderr_task) if task is not None
        )
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        error = self._fatal_error or PiProtocolError("Pi RPC client closed.")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    def _raise_if_unavailable(self) -> None:
        if self._fatal_error is not None:
            raise self._fatal_error
        if self._closed:
            raise PiProtocolError("Pi RPC stdin is closed.")

    async def _write_json(self, value: Mapping[str, object]) -> None:
        self._raise_if_unavailable()
        encoded = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        async with self._write_lock:
            self._stdin.write(encoded)
            try:
                await self._stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                error = PiProtocolError("Pi RPC stdin closed while writing.")
                await self._fail(error)
                raise error from exc

    async def _read_stdout(self) -> None:
        try:
            while True:
                try:
                    line = await self._stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as exc:
                    raise PiProtocolError(
                        f"Pi RPC JSONL line exceeds {self._max_line_bytes} bytes."
                    ) from exc
                if not line:
                    await self._flush_text()
                    if self._pending:
                        raise PiProtocolError(
                            "Pi RPC stdout ended with pending command responses."
                        )
                    await self._events.put(None)
                    return
                if not line.endswith(b"\n"):
                    raise PiProtocolError("Pi RPC stdout ended with an incomplete JSONL line.")
                raw = line[:-1]
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                if len(raw) > self._max_line_bytes:
                    raise PiProtocolError(
                        f"Pi RPC JSONL line exceeds {self._max_line_bytes} bytes."
                    )
                try:
                    decoded = raw.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise PiProtocolError("Pi RPC stdout contains invalid UTF-8.") from exc
                try:
                    value = json.loads(decoded)
                except json.JSONDecodeError as exc:
                    raise PiProtocolError("Pi RPC stdout contains invalid JSON.") from exc
                if not isinstance(value, dict):
                    raise PiProtocolError("Pi RPC JSON value must be an object.")
                event_type = value.get("type")
                if not isinstance(event_type, str) or not event_type:
                    raise PiProtocolError("Pi RPC JSON object is missing a string type.")
                await self._handle_object(value)
        except PiProtocolError as error:
            await self._fail(error)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            await self._fail(PiProtocolError(f"Pi RPC reader failed: {exc}"))

    async def _handle_object(self, value: dict[str, Any]) -> None:
        event_type = value["type"]
        if event_type == "response":
            await self._flush_text()
            response_id = value.get("id")
            if not isinstance(response_id, str) or response_id not in self._pending:
                raise PiProtocolError(f"Pi RPC returned unknown response id: {response_id}")
            future = self._pending.pop(response_id)
            if future.done():
                raise PiProtocolError(f"Pi RPC returned duplicate response id: {response_id}")
            if value.get("success") is True:
                future.set_result(value)
            elif value.get("success") is False and isinstance(value.get("error"), str):
                future.set_exception(PiCommandError(value["error"]))
            else:
                raise PiProtocolError("Pi RPC response has invalid success/error fields.")
            return

        if event_type == "message_update":
            delta = value.get("assistantMessageEvent")
            if (
                isinstance(delta, dict)
                and delta.get("type") == "text_delta"
                and isinstance(delta.get("delta"), str)
            ):
                await self._append_text(delta["delta"])
                return

        await self._flush_text()
        if event_type in _KNOWN_EVENT_TYPES:
            await self._events.put(PiEvent(type=event_type, payload=dict(value)))
        # Unknown but well-formed events are intentionally ignored.

    async def _append_text(self, delta: str) -> None:
        self._text_buffer += delta
        while len(self._text_buffer.encode("utf-8")) >= self._output_chunk_bytes:
            prefix, remainder = self._split_utf8_prefix(
                self._text_buffer, self._output_chunk_bytes
            )
            self._text_buffer = remainder
            await self._events.put(PiEvent("text_output", {"text": prefix}))

    @staticmethod
    def _split_utf8_prefix(value: str, max_bytes: int) -> tuple[str, str]:
        used = 0
        index = 0
        for index, character in enumerate(value, start=1):
            encoded_size = len(character.encode("utf-8"))
            if used + encoded_size > max_bytes:
                index -= 1
                break
            used += encoded_size
        else:
            index = len(value)
        if index == 0:
            index = 1
        return value[:index], value[index:]

    async def _flush_text(self) -> None:
        if self._text_buffer:
            text, self._text_buffer = self._text_buffer, ""
            await self._events.put(PiEvent("text_output", {"text": text}))

    async def _drain_stderr(self) -> None:
        try:
            while chunk := await self._stderr.read(8192):
                self._stderr_tail.extend(chunk)
                overflow = len(self._stderr_tail) - self._stderr_tail_bytes
                if overflow > 0:
                    del self._stderr_tail[:overflow]
        except asyncio.CancelledError:
            raise

    async def _fail(self, error: PiProtocolError) -> None:
        if self._fatal_error is not None:
            return
        self._fatal_error = error
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        await self._events.put(error)


__all__ = [
    "PiCommandError",
    "PiCommandTimeout",
    "PiEvent",
    "PiProtocolError",
    "PiRpcClient",
    "PiRpcError",
]
