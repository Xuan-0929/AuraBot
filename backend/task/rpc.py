"""实现 Task runtime 使用的严格 Pi stdin/stdout JSONL 协议。

PiRpcClient 只拥有通信状态：串行写命令、关联 response、在一个 stdout reader 中验证
framing 并向 Manager 提供有界事件流；同文件的小函数归一化 Manager 消费的 Extension
UI 和 agent_end payload。process.py 拥有 subprocess；本模块不认识 Task 状态或进程。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any


class PiRpcError(RuntimeError):
    """Pi RPC 边界错误。"""


class PiProtocolError(PiRpcError):
    """不可恢复的 framing、payload 或 response correlation 错误。"""


class PiCommandError(PiRpcError):
    """格式合法的 Pi response 拒绝了命令。"""


class PiCommandTimeout(PiCommandError):
    """Pi 未在期限内回复命令。"""


@dataclass(frozen=True, slots=True)
class PiEvent:
    """Manager 消费的最小归一化 Pi 事件。"""

    type: str
    payload: Mapping[str, Any]


_KNOWN_EVENTS = frozenset(
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
QUESTION_MARKER = "AURABOT_TASK_QUESTION_V1\n"


def parse_question_request(payload: object) -> tuple[str, tuple[str, ...]] | None:
    """提取 AuraBot input/select 问题；第三方 dialog 返回 None。"""
    if not isinstance(payload, Mapping):
        return None
    method, title = payload.get("method"), payload.get("title")
    if not (method in {"input", "select"} and isinstance(title, str)
            and title.startswith(QUESTION_MARKER)):
        return None
    raw = payload.get("options")
    options = tuple(item for item in raw if isinstance(item, str)) if isinstance(raw, list) else ()
    return title[len(QUESTION_MARKER):], options


def dialog_response_id(payload: object) -> str | None:
    """返回需要取消的合法 Pi dialog id。"""
    if not isinstance(payload, Mapping) or payload.get("method") not in {
        "input", "select", "confirm", "editor",
    }:
        return None
    request_id = payload.get("id")
    return request_id if isinstance(request_id, str) else None


def agent_end_has_model_error(payload: object) -> bool:
    """只检查 agent_end 中 assistant 的最终 error stop reason。"""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("messages"), list):
        return False
    return any(
        isinstance(message, Mapping) and message.get("role") == "assistant"
        and message.get("stopReason") == "error"
        for message in reversed(payload["messages"])
    )


class PiRpcClient:
    """绑定 process 提供的 stdin/stdout，管理严格 JSONL 通信。"""

    def __init__(
        self,
        *,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
        response_timeout_seconds: float,
        max_line_bytes: int,
        event_queue_size: int,
        output_chunk_bytes: int,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._response_timeout = response_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._output_chunk_bytes = output_chunk_bytes
        self._events: asyncio.Queue[PiEvent | PiProtocolError | None] = asyncio.Queue(
            maxsize=event_queue_size
        )
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task[None] | None = None
        self._next_command_id = 1
        self._fatal_error: PiProtocolError | None = None
        self._text_buffer = ""
        self._closed = False

    @property
    def command_timeout_seconds(self) -> float:
        return self._response_timeout

    async def start(self) -> None:
        """幂等启动唯一 stdout reader。"""
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(
                self._read_stdout(), name="pi-rpc-stdout"
            )

    async def command(
        self,
        command_type: str,
        payload: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        """发送带唯一 id 的命令并等待对应 response。"""
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
        except TimeoutError as error:
            self._pending.pop(command_id, None)
            raise PiCommandTimeout(f"Pi RPC command {command_type} timed out.") from error
        except BaseException:
            self._pending.pop(command_id, None)
            raise

    async def events(self) -> AsyncIterator[PiEvent]:
        """按协议顺序产出事件；EOF 结束，fatal error 在消费者处抛出。"""
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
        if self._closed:
            return
        self._closed = True
        self._stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await self._stdin.wait_closed()

    async def aclose(self) -> None:
        """关闭协议端和 reader；subprocess 仍必须由 process.py reap。"""
        await self.close_stdin()
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
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
            try:
                self._stdin.write(encoded)
                await self._stdin.drain()
            except OSError as error:
                protocol_error = PiProtocolError("Pi RPC stdin closed while writing.")
                await self._fail(protocol_error)
                raise protocol_error from error

    async def _read_stdout(self) -> None:
        try:
            while True:
                try:
                    line = await self._stdout.readline()
                except (ValueError, asyncio.LimitOverrunError) as error:
                    raise PiProtocolError(
                        f"Pi RPC JSONL line exceeds {self._max_line_bytes} bytes."
                    ) from error
                if not line:
                    await self._flush_text()
                    if self._pending:
                        raise PiProtocolError(
                            "Pi RPC stdout ended with pending command responses."
                        )
                    await self._events.put(None)
                    return
                value = self._decode_line(line)
                await self._handle_object(value)
        except PiProtocolError as error:
            await self._fail(error)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            await self._fail(PiProtocolError(f"Pi RPC reader failed: {error}"))

    def _decode_line(self, line: bytes) -> dict[str, Any]:
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
        except UnicodeDecodeError as error:
            raise PiProtocolError("Pi RPC stdout contains invalid UTF-8.") from error
        try:
            value = json.loads(decoded)
        except json.JSONDecodeError as error:
            raise PiProtocolError("Pi RPC stdout contains invalid JSON.") from error
        if not isinstance(value, dict):
            raise PiProtocolError("Pi RPC JSON value must be an object.")
        event_type = value.get("type")
        if not isinstance(event_type, str) or not event_type:
            raise PiProtocolError("Pi RPC JSON object is missing a string type.")
        return value

    async def _handle_object(self, value: dict[str, Any]) -> None:
        event_type = value["type"]
        if event_type == "response":
            await self._flush_text()
            response_id = value.get("id")
            if not isinstance(response_id, str) or response_id not in self._pending:
                raise PiProtocolError(
                    f"Pi RPC returned unknown response id: {response_id}"
                )
            future = self._pending.pop(response_id)
            if value.get("success") is True:
                future.set_result(value)
            elif value.get("success") is False and isinstance(value.get("error"), str):
                future.set_exception(PiCommandError(value["error"]))
            else:
                raise PiProtocolError(
                    "Pi RPC response has invalid success/error fields."
                )
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
        if event_type in _KNOWN_EVENTS:
            await self._events.put(PiEvent(event_type, dict(value)))
        # Unknown well-formed events are forward-compatible and intentionally ignored.

    async def _append_text(self, delta: str) -> None:
        self._text_buffer += delta
        while len(self._text_buffer.encode("utf-8")) >= self._output_chunk_bytes:
            prefix, self._text_buffer = self._split_utf8_prefix(
                self._text_buffer, self._output_chunk_bytes
            )
            await self._events.put(PiEvent("text_output", {"text": prefix}))

    @staticmethod
    def _split_utf8_prefix(value: str, max_bytes: int) -> tuple[str, str]:
        used = index = 0
        for index, character in enumerate(value, start=1):
            size = len(character.encode("utf-8"))
            if used + size > max_bytes:
                index -= 1
                break
            used += size
        else:
            index = len(value)
        index = max(index, 1)
        return value[:index], value[index:]

    async def _flush_text(self) -> None:
        if self._text_buffer:
            text, self._text_buffer = self._text_buffer, ""
            await self._events.put(PiEvent("text_output", {"text": text}))

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
    "agent_end_has_model_error",
    "dialog_response_id",
    "parse_question_request",
]
