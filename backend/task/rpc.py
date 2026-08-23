"""
实现 AuraBot 所需的严格 Pi stdin/stdout JSONL 协议子集。

一个 PiRpcClient 绑定一个已经由 PiProcessSupervisor 创建的 Pi 子进程字节流。它把
Python command 编码成 LF 结尾的 JSON 行，按 command id 等待对应 response，并把 Pi
连续输出的事件转换成 Task 执行层可消费的 PiEvent 异步流。stdout 是严格协议通道，
stderr 则被持续读取并只保留有限诊断尾部，避免子进程因管道写满而阻塞。

本模块不创建或终止进程，也不理解 queued、running、waiting 等 Task 产品状态。进程句柄
由 Supervisor 管理，TaskManager 消费归一化事件；任何 framing、UTF-8、JSON 或 response
correlation 破坏都会使当前 RPC Client 进入不可恢复的 fatal protocol error 状态。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class _AsyncWriter(Protocol):
    """描述 PiRpcClient 写入子进程 stdin 所需的最小异步接口。"""

    def write(self, data: bytes) -> None:
        """
        把字节交给底层传输缓冲区；实际发送完成由 ``drain()`` 等待。
        write只是把数据放入缓冲区(stdOut)，接下来交给OS。
        所以是瞬间完成，不需要异步。
        """

        ...

    async def drain(self) -> None:
        """
        如果Pi(stdIN端)读缓冲区很慢，那缓冲区会满。

        drain等待写缓冲区降到安全水位，用背压限制持续写入速度。
        """

        ...

    def close(self) -> None:
        """请求关闭 stdin 写端，使 Pi 能观察 EOF 并自行退出。"""

        ...

    async def wait_closed(self) -> None:
        """等待 stdin 写端完成关闭；这不等价于等待 Pi 子进程退出。"""

        ...


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
    """绑定一个外部所有者管理的 Pi 子进程，提供严格 JSONL command/event 通信。

    Client 内部有两个长期后台协程：stdout reader 解析协议，stderr drainer 防止诊断管道
    堵塞。调用方通过 ``command()`` 等待单个 response，通过 ``events()`` 持续消费事件。
    Client 只拥有通信状态，不拥有 subprocess handle，也不能决定 Task 的业务生命周期。
    """

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
        """保存一个 Pi 进程的三条标准流和协议容量限制。

        ``stdin`` 用于串行写入 command 与 Extension UI response；``stdout`` 只能包含 Pi
        JSONL 协议；``stderr`` 作为非结构化诊断流独立读取。构造阶段不创建后台协程，调用
        方必须随后执行 ``start()``。队列、行长度、stderr 尾部和文本块大小均设置上限，
        避免异常 Pi 输出导致 Python 进程无界占用内存。
        """

        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self._response_timeout = response_timeout_seconds
        self._max_line_bytes = max_line_bytes
        self._stderr_tail_bytes = stderr_tail_bytes
        self._output_chunk_bytes = output_chunk_bytes

        # stdout reader 把普通 PiEvent、fatal error 和正常 EOF 哨兵写入同一条有界队列；
        # events() 是这条队列的消费端，因此消费过慢时会自然向 stdout reader 施加背压。
        self._events: asyncio.Queue[PiEvent | PiProtocolError | None] = asyncio.Queue(
            maxsize=event_queue_size
        )

        # 每个已发送但尚未收到 response 的 command id 对应一个 Future。stdout reader
        # 收到 response 后按 id 完成它，使多个并发 command 不会串错返回值。
        self._pending: dict[str, asyncio.Future[Mapping[str, Any]]] = {}

        # 多个协程可能同时发送 command 或 UI response；写锁保证每一行 JSONL 不会交叉。
        self._write_lock = asyncio.Lock()

        # 这两个 Task 只管理协议读端；Pi 子进程的 wait/terminate/kill 仍由 Supervisor 管理。
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

        self._next_command_id = 1
        
        self._fatal_error: PiProtocolError | None = None

        # Pi 会逐个发送 text_delta；这里先合并，再按 UTF-8 字节上限产生 text_output。
        self._text_buffer = ""

        # stderr 只保存最后 N 字节供日志诊断，不进入 Task 产品事件或公开错误响应。
        self._stderr_tail = bytearray()
        self._closed = False

    @property
    def stderr_tail(self) -> bytes:
        """返回当前保留的有限 stderr 尾部副本，仅供结构化诊断日志使用。

        返回 ``bytes`` 副本，调用方不能修改 Client 内部缓冲区。该内容可能包含底层运行
        细节，不应直接写入 Task 公共 Event、API error message 或模型上下文。
        """

        return bytes(self._stderr_tail)

    async def start(self) -> None:
        """为当前 Client 启动唯一的 stdout parser 和 stderr drainer。

        stdout parser 负责完成 command Future 和产生 PiEvent；stderr drainer 只防止管道
        堵塞并保留诊断尾部。重复调用保持幂等，不会创建第二组 reader。此方法只启动后台
        协程，不发送 ``get_state`` 或 ``prompt``，这些命令由 PiProcessSupervisor 调用。
        """

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
        """发送一项带唯一 id 的 Pi command，并只等待与该 id 对应的 response。

        方法先检查 Client 是否可用，为 command 创建 Future 并登记到 ``_pending``，然后
        通过 ``_write_json()`` 写入一行 JSONL。stdout reader 收到 response 后根据 id
        完成对应 Future，因此多个协程可以并发等待不同 command，而不会依赖返回顺序。

        Pi 返回 ``success: false`` 时抛出 ``PiCommandError``；超过配置期限时移除 pending
        项并抛出 ``PiCommandTimeout``；Client 已关闭或协议损坏时抛出
        ``PiProtocolError``。无论何种异常，本 command 都不会残留在 ``_pending`` 中。
        """

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
        """按到达顺序持续产出归一化 PiEvent，直到 stdout 正常结束或协议失败。

        队列中的 ``PiEvent`` 被 yield 给 Task 执行层；``None`` 是 stdout 正常 EOF 哨兵，
        会结束异步迭代；``PiProtocolError`` 是 fatal error 哨兵，会在消费者协程中重新
        抛出。调用方通常以 ``async for`` 或 ``anext()`` 消费本方法返回的异步迭代器。
        """

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
        """向 Pi 回复或取消一项 Extension UI 请求，不创建 command Future。

        该消息使用 Extension 原请求 id：``None`` 编码为 cancelled，布尔值编码为
        confirmed，其他值编码为字符串 value。Pi RPC 不为这种消息返回普通 command
        response，因此这里只保证 JSONL 已成功写入底层流，不等待 Tool 已恢复执行。
        """

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
        """幂等关闭 Pi 子进程 stdin，使子进程能够观察 EOF。

        Broken Pipe 或 Connection Reset 表示对端已经关闭，关闭阶段会忽略这两类错误。
        本方法只关闭 RPC 写端，不取消 stdout/stderr reader，也不 ``wait()``、terminate
        或 kill 子进程；完整进程回收仍由 PiProcessSupervisor 负责。
        """

        if self._closed:
            return
        self._closed = True
        self._stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await self._stdin.wait_closed()

    async def aclose(self) -> None:
        """关闭通信端并取消后台 reader，但不等待或终止 Pi 子进程。

        方法先幂等关闭 stdin，再取消并等待 stdout/stderr 后台协程。所有仍在等待 response
        的 command Future 会以既有 fatal error 或通用 ``Pi RPC client closed`` 错误
        结束，防止调用方永久挂起。进程句柄不属于本对象，调用方仍必须通过 Supervisor
        完成 abort、terminate、kill 与 reap。
        """

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
        """在写入前拒绝已经 fatal 或已关闭的 RPC Client。

        首次 fatal protocol error 会被保存在 ``_fatal_error``，后续所有写操作都重新
        抛出同一个错误；正常关闭后则抛出 stdin 已关闭的 ``PiProtocolError``。该检查
        避免在已知不可恢复的连接上继续登记或发送新消息。
        """

        if self._fatal_error is not None:
            raise self._fatal_error
        if self._closed:
            raise PiProtocolError("Pi RPC stdin is closed.")

    async def _write_json(self, value: Mapping[str, object]) -> None:
        """把一个对象编码成紧凑 UTF-8 JSONL，并串行写入 Pi stdin。

        每条消息严格追加一个 LF。``_write_lock`` 防止两个发送协程把字节交叉写入同一
        行；``drain()`` 提供写侧背压。Broken Pipe 或 Connection Reset 会转成 fatal
        ``PiProtocolError``，同时通过 ``_fail()`` 通知 pending command 和 event consumer。
        """

        self._raise_if_unavailable()
        encoded = (
            json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        async with self._write_lock:
            try:
                self._stdin.write(encoded)
                await self._stdin.drain()
            except OSError as exc:
                error = PiProtocolError("Pi RPC stdin closed while writing.")
                await self._fail(error)
                raise error from exc

    async def _read_stdout(self) -> None:
        """后台读取 Pi stdout，验证 JSONL framing，并分派每个协议对象。

        每行必须在容量限制内、以 LF 完整结束、使用合法 UTF-8、解析为 JSON object，且
        包含非空字符串 ``type``。正常 EOF 前会刷新未满块的文本；如果 EOF 时仍有
        pending command，则说明 Pi 未返回必要 response，被视为协议错误。任何
        ``PiProtocolError`` 都交给 ``_fail()`` 广播；协程被显式取消时保留取消语义。
        """

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
        """把一个已通过 framing 验证的 Pi 对象路由为 response、文本或普通事件。

        ``response`` 按 id 完成对应 pending Future，并严格验证 success/error 字段；
        ``message_update`` 中的 ``text_delta`` 进入文本合并缓冲区；已知生命周期事件被
        包装为 ``PiEvent`` 放入有界队列。遇到非文本对象前会先 flush 已累积文本，保持
        文本与后续事件的相对顺序。未知但格式合法的事件有意忽略，以允许 Pi 增加本轮
        AuraBot 不消费的新事件类型。
        """

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
        """合并 Pi text delta，并按 UTF-8 字节上限产生 ``text_output`` Event。

        Pi 可能以很小的字符增量发送模型输出，逐 delta 持久化会产生大量 Event，因此先
        放入 ``_text_buffer``。当缓冲区达到 ``_output_chunk_bytes`` 时，按完整 Unicode
        字符边界切出前缀并写入事件队列；不足一个块的尾部继续保留等待后续 delta。
        """

        self._text_buffer += delta
        while len(self._text_buffer.encode("utf-8")) >= self._output_chunk_bytes:
            prefix, remainder = self._split_utf8_prefix(
                self._text_buffer, self._output_chunk_bytes
            )
            self._text_buffer = remainder
            await self._events.put(PiEvent("text_output", {"text": prefix}))

    @staticmethod
    def _split_utf8_prefix(value: str, max_bytes: int) -> tuple[str, str]:
        """按 UTF-8 字节预算切分字符串，同时保证不截断 Unicode 字符。

        返回 ``(prefix, remainder)``。前缀尽量不超过 ``max_bytes``；如果单个字符本身就
        超过预算，仍至少返回该完整字符，确保调用方能够向前推进而不会无限循环。这里按
        字符逐个计算编码长度，是因为 Python 字符索引与 UTF-8 字节偏移并不相同。
        """

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
        """把当前尚未达到块大小的文本尾部立即转换为一个 ``text_output`` Event。

        response、普通生命周期事件或 stdout EOF 到达前调用本方法，以保证此前收到的
        text delta 先于后续对象被消费者观察。缓冲区为空时保持幂等，不产生空事件。
        """

        if self._text_buffer:
            text, self._text_buffer = self._text_buffer, ""
            await self._events.put(PiEvent("text_output", {"text": text}))

    async def _drain_stderr(self) -> None:
        """后台持续读取 Pi stderr，并只保留最近的有限字节用于诊断。

        子进程 stderr 使用管道时，如果父进程不读取且缓冲区被写满，Pi 可能被反向阻塞；
        因此本协程持续 drain。超过 ``_stderr_tail_bytes`` 的旧内容从左侧丢弃。stderr
        不是 JSONL 协议，读取结束或普通诊断内容都不会产生 PiEvent；显式取消仍向上传播。
        """

        try:
            while chunk := await self._stderr.read(8192):
                self._stderr_tail.extend(chunk)
                overflow = len(self._stderr_tail) - self._stderr_tail_bytes
                if overflow > 0:
                    del self._stderr_tail[:overflow]
        except asyncio.CancelledError:
            raise

    async def _fail(self, error: PiProtocolError) -> None:
        """把首次 fatal protocol error 广播给所有 RPC 等待者和事件消费者。

        Client 一旦 fatal 就不可恢复：保存首个错误，令所有 pending command Future 以
        同一错误结束，清空 correlation 表，并把错误放入事件队列，使正在消费
        ``events()`` 的 Task execution 也观察到失败。后续错误不会覆盖首个根因，也不会
        重复向队列写入 fatal 哨兵。
        """

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
