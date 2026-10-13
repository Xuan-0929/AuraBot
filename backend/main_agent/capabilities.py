"""Own Profile-scoped conversation capabilities independently of voice turn detection.

ConversationMemory handles recall/prefetch and the two-stage idle archive lifecycle against
the existing MemoryService and canonical ContextStore. Engines report user activity and
playback settlement; only the idle timer is cancelled by new input, while a frozen archive
keeps its stable source identity until storage succeeds. Pipeline-specific grant retirement
is an optional lifecycle callback and never enters the capability's public data model.
ConversationCapabilities shares validated tools, Task ownership and invocation deduplication
between engines. Cancelling a voice waiter does not revoke an accepted business operation.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .context import ContextStore, PendingConversationArchive
from .metrics import speech_logger
from .tool_runtime import ToolCall, ToolDefinition, ToolExecutorError, ToolSurfacePolicy, EffectClass
from .tool_runtime.definitions import _matches


class ConversationCapabilities:
    def __init__(self, context: ContextStore, memory: "ConversationMemory", policy: ToolSurfacePolicy,
                 executors: dict[str, Any], tasks: Any = None) -> None:
        self.context, self.memory, self.policy = context, memory, policy
        self.executors, self.tasks = dict(executors), tasks
        self._calls: dict[tuple[str, str], tuple[str, asyncio.Task[Any]]] = {}
        self._closed = False
        self._reads = asyncio.Semaphore(3)
        if tasks is not None:
            self.executors.update({name: tasks.execute_control for name in (
                "answer_task_question", "pause_task", "resume_task", "cancel_task", "delete_task", "rerun_task")})
            self.executors["inspect_tasks"] = tasks.bridge.inspect_tasks
        self.executors["memory_recall"] = self._recall

    def definitions(self) -> tuple[ToolDefinition, ...]:
        definitions = [d for d in self.policy.definitions if d.name in self.executors or (d.name == "task_assign" and self.tasks is not None)]
        if self.memory.service is not None:
            definitions.append(ToolDefinition("memory_recall", "Recall the current Profile's long-term memories relevant to a query.", EffectClass.SAFE_READ,
                {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 1000}}, "required": ["query"], "additionalProperties": False}))
        return tuple(definitions)

    async def _recall(self, call: ToolCall) -> Any:
        return await self.memory.recall(call.arguments["query"])

    async def execute(self, scope: str, call: ToolCall, *, admit: bool = True) -> Any:
        if self._closed:
            raise ToolExecutorError("capabilities_closed")
        definition = next((d for d in self.definitions() if d.name == call.name), None)
        if definition is None:
            raise ToolExecutorError("tool_not_exposed")
        if call.parse_error or not _matches(call.arguments, definition.parameters):
            raise ToolExecutorError("schema_invalid")
        identity = (scope, call.call_id)
        fingerprint = json.dumps([call.name, call.arguments], sort_keys=True, ensure_ascii=False, allow_nan=False)
        previous = self._calls.get(identity)
        if previous is not None:
            if previous[0] != fingerprint:
                raise ToolExecutorError("duplicate_call_conflict")
            return await asyncio.shield(previous[1])
        if not admit:
            raise ToolExecutorError("reply_expired")
        if len(self._calls) >= 4096:
            raise ToolExecutorError("tool_session_limit")

        async def run() -> Any:
            started = time.monotonic()
            try:
                if call.name == "task_assign":
                    async with self.tasks.lock:
                        return self.tasks.propose(scope, call)
                if definition.effect is EffectClass.SAFE_READ:
                    async with asyncio.timeout(30), self._reads:
                        return await self.executors[call.name](call)
                return await self.executors[call.name](call)
            finally:
                speech_logger.info("conversation_tool profile=%s scope=%s call=%s name=%s elapsed_ms=%.1f",
                    self.memory.profile_id, scope, call.call_id, call.name, (time.monotonic() - started) * 1000)

        worker = asyncio.create_task(run(), name=f"conversation-tool-{call.name}")
        worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        self._calls[identity] = (fingerprint, worker)
        return await asyncio.shield(worker)

    def retire_scope(self, scope: str) -> None:
        for identity, (_, worker) in tuple(self._calls.items()):
            if identity[0] != scope:
                continue
            if worker.done():
                self._calls.pop(identity, None)
            else:
                worker.add_done_callback(lambda _, key=identity: self._calls.pop(key, None))

    async def shutdown(self) -> None:
        self._closed = True
        await asyncio.gather(*(task for _, task in self._calls.values()), return_exceptions=True)
        self._calls.clear()


async def _settled() -> None:
    pass


class ConversationMemory:
    def __init__(self, context: ContextStore, service: Any, profile_id: str | None, *,
                 idle_seconds: float = 300, wall_clock: Callable[[], float] = time.time,
                 lock: asyncio.Lock | None = None, eligible: Callable[[], bool] = lambda: True,
                 wait_settled: Callable[[], Awaitable[None]] = _settled,
                 on_seal: Callable[[], None] = lambda: None,
                 on_commit: Callable[[], None] = lambda: None) -> None:
        self.context, self.service, self.profile_id = context, service, profile_id
        self.idle_seconds, self.wall_clock = idle_seconds, wall_clock
        self.lock, self.eligible, self.wait_settled = lock or asyncio.Lock(), eligible, wait_settled
        self.on_seal, self.on_commit = on_seal, on_commit
        self.idle_task: asyncio.Task[None] | None = None
        self.archive_task: asyncio.Task[None] | None = None
        self._prefetch_task: asyncio.Task[None] | None = None
        self._cache: tuple[str, int, dict[str, Any]] | None = None
        self._query_version = 0
        self._active = False
        self._closed = False
        self.playback_settled = asyncio.Event()
        self.playback_settled.set()

    def delivery_changed(self) -> None:
        if self.context.has_unsettled_clauses():
            self.playback_settled.clear()
        else:
            self.playback_settled.set()

    @property
    def writable(self) -> bool:
        return self.service is not None and not getattr(self.service, "read_only", False) and not self._closed

    async def recall(self, query: str, *, max_chars: int = 2400) -> dict[str, Any]:
        if self.service is None:
            return {"text": "", "memory_ids": []}
        if self._cache is not None and self._cache[:2] == (query, max_chars):
            return self._cache[2]
        return await self.service.recall_context_async(profile_id=self.profile_id, query=query, limit=6, max_chars=max_chars)

    def prefetch(self, query: str, *, max_chars: int = 2400) -> None:
        self._query_version += 1
        version = self._query_version
        self._cache = None
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
        async def run() -> None:
            try:
                async with asyncio.timeout(2):
                    result = await self.recall(query, max_chars=max_chars)
                if version == self._query_version and not self._closed:
                    self._cache = (query, max_chars, result)
            except Exception:
                speech_logger.info("realtime_memory_prefetch_failed profile=%s", self.profile_id)
        self._prefetch_task = asyncio.create_task(run(), name="conversation-memory-prefetch")

    def activity_started(self) -> None:
        self._active = True
        self.cancel_idle()

    def confirm_activity(self) -> None:
        self._active = False
        if self.writable and self.eligible():
            self.context.set_last_user_activity(self.wall_clock())
            self.schedule(self.idle_seconds)

    def resume(self) -> None:
        if not self.writable or self._active or not self.eligible():
            return
        if self.context.pending_conversation_archive() is None and self.context.conversation_batch() is None:
            return
        last = self.context.get_last_user_activity()
        if last is None:
            last = self.wall_clock()
            self.context.set_last_user_activity(last)
        self.schedule(max(0, last + self.idle_seconds - self.wall_clock()))

    def end_activity(self) -> None:
        self._active = False
        self.resume()

    def schedule(self, delay: float) -> None:
        if not self.writable:
            return
        self.cancel_idle()
        self.idle_task = asyncio.create_task(self._after_idle(delay), name=f"memory-idle-{self.profile_id}")

    def cancel_idle(self) -> None:
        task, self.idle_task = self.idle_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def cancel_archive(self) -> None:
        task, self.archive_task = self.archive_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _after_idle(self, delay: float) -> None:
        current = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.wait_settled()
            async with self.lock:
                if not self.writable or self._active or not self.eligible() or self.context.has_unsettled_clauses():
                    return
                pending = self.context.pending_conversation_archive()
                if pending is None:
                    pending = self.context.freeze_conversation_archive()
                    if pending is not None:
                        self.on_seal()
                if pending is None:
                    self.context.clear_last_user_activity_if_idle()
                    return
                if self.idle_task is current:
                    self.idle_task = None
                if self.archive_task is None or self.archive_task.done():
                    self.archive_task = asyncio.create_task(self._archive(pending), name=f"memory-archive-{self.profile_id}")
        finally:
            if self.idle_task is current:
                self.idle_task = None

    async def _archive(self, pending: PendingConversationArchive) -> None:
        current = asyncio.current_task()
        try:
            result = await self.service.remember_conversation(profile_id=self.profile_id,
                turns=self.context.conversation_archive_turns(pending), source_id=pending.source_id)
            async with self.lock:
                if self._closed:
                    return
                self.context.commit_conversation_archive(pending, result["summary"])
                self._cache = None
                self.on_commit()
                if self.archive_task is current:
                    self.archive_task = None
                self.resume()
        except Exception:
            speech_logger.warning("conversation_archive_failed profile=%s", self.profile_id)
            async with self.lock:
                if not self._closed and (self.idle_task is None or self.idle_task.done()):
                    self.schedule(self.idle_seconds)
        finally:
            if self.archive_task is current:
                self.archive_task = None

    async def shutdown(self) -> None:
        self._closed = True
        tasks = [task for task in (self.idle_task, self.archive_task, self._prefetch_task) if task is not None]
        self.cancel_idle()
        self.cancel_archive()
        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
