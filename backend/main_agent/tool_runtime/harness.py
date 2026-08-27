"""Run one strictly single-flight Main Agent tool lifecycle with deterministic policy enforcement.

ToolHarness consumes provider chunks in-process, streams stable speech clauses through an injected
speech port, stages every call for complete JSON/schema/group validation, executes say before other
effects regardless of provider order, and re-reads scheduler fencing facts at each acceptance point.
Safe Observation origin ownership/reuse is synchronously decided exactly when a validated call
detaches; one process-shared owner admits at most three executor jobs across Harness instances. Running
request identities are never evicted, while terminal dedupe/outcome diagnostics use hard bounded windows;
cancellation and all control effects still settle before another control invoke is admitted.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from collections import OrderedDict, deque
from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from typing import Any

from .definitions import (
    EffectClass,
    EffectFence,
    InvokeMode,
    InvokeSettlement,
    ObservationOutcome,
    ResponseGrant,
    StreamedSpeechReceipt,
    ToolCall,
    ToolExecutorError,
    ToolReceipt,
    ToolSurface,
    ToolSurfacePolicy,
)
from .stream import SayClause, ToolCallAssembler, segment_say_text


Executor = Callable[[ToolCall], Any]


class ObservationLimiter:
    """Process-shareable nonblocking admission owner for the fixed live Observation budget."""

    def __init__(self, limit: int = 3) -> None:
        if limit < 1:
            raise ValueError("Observation limit must be positive")
        self.limit = limit
        self._active = 0
        self._lock = threading.Lock()

    @property
    def active(self) -> int:
        with self._lock:
            return self._active

    def try_acquire(self) -> bool:
        with self._lock:
            if self._active >= self.limit:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._lock:
            if self._active < 1:
                raise RuntimeError("Observation limiter released without ownership")
            self._active -= 1


_GLOBAL_OBSERVATION_LIMITER = ObservationLimiter(3)
_OBSERVATION_HISTORY_LIMIT = 256


class ToolHarness:
    """Small async coordinator for provider parsing, tool policy, execution, and settlement."""

    def __init__(
        self,
        policy: ToolSurfacePolicy,
        executors: Mapping[str, Executor],
        *,
        state_provider: Callable[[], EffectFence | Mapping[str, Any]] | None = None,
        observation_acceptance_hook: Callable[[str, ToolCall], Any] | None = None,
        observation_completion_hook: Callable[[ObservationOutcome], Any] | None = None,
        cancel_tombstone_limit: int = 256,
        observation_limiter: ObservationLimiter | None = None,
    ) -> None:
        if cancel_tombstone_limit < 1:
            raise ValueError("cancel_tombstone_limit must be positive")
        self.policy = policy
        self.executors = dict(executors)
        self.state_provider = state_provider
        self.observation_acceptance_hook = observation_acceptance_hook
        self.observation_completion_hook = observation_completion_hook
        self.cancel_tombstone_limit = cancel_tombstone_limit
        self.observation_limiter = observation_limiter or _GLOBAL_OBSERVATION_LIMITER
        self._single_flight = asyncio.Lock()
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._control_tasks: dict[str, asyncio.Task[Any]] = {}
        self._observation_tasks: set[asyncio.Task[Any]] = set()
        self._observation_outcomes: deque[ObservationOutcome] = deque(
            maxlen=_OBSERVATION_HISTORY_LIMIT
        )
        self._observation_requests: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._active_observation_requests: set[tuple[str, str, str]] = set()
        self._observation_task_keys: dict[asyncio.Task[Any], tuple[str, str, str]] = {}
        self._cancelled_invokes: OrderedDict[str, None] = OrderedDict()

    def freeze_surface(
        self,
        mode: InvokeMode,
        *,
        input_id: str,
        revision: int,
        context_version: int,
        response_grant: ResponseGrant | None = None,
        current_response_grant: ResponseGrant | None = None,
        speech_candidate: bool = False,
        confirmed_speech: bool = False,
    ) -> ToolSurface:
        """Expose the exact schemas to place in an immutable Task-2 Snapshot."""

        return self.policy.freeze_surface(
            mode,
            input_id=input_id,
            revision=revision,
            context_version=context_version,
            response_grant=response_grant,
            current_response_grant=current_response_grant,
            speech_candidate=speech_candidate,
            confirmed_speech=confirmed_speech,
        )

    def cancel(self, invoke_id: str) -> None:
        """Fence an active invoke and interrupt its currently awaited control executor/provider read."""

        self._cancelled_invokes[invoke_id] = None
        self._cancelled_invokes.move_to_end(invoke_id)
        while len(self._cancelled_invokes) > self.cancel_tombstone_limit:
            self._cancelled_invokes.popitem(last=False)
        event = self._cancel_events.get(invoke_id)
        if event is not None:
            event.set()
        task = self._control_tasks.get(invoke_id)
        if task is not None:
            task.cancel()

    async def wait_for_observations(self) -> tuple[ObservationOutcome, ...]:
        """Test/shutdown hook that waits only for already detached Observation executor calls."""

        if self._observation_tasks:
            await asyncio.gather(*tuple(self._observation_tasks), return_exceptions=True)
        return tuple(self._observation_outcomes)

    async def cancel_observations(self) -> tuple[ObservationOutcome, ...]:
        """Cancel accepted jobs during runtime shutdown and retain their failed terminal outcomes."""

        tasks = tuple(self._observation_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return tuple(self._observation_outcomes)

    @asynccontextmanager
    async def _invoke_scope(self, invoke_id: str) -> AsyncIterator[None]:
        """Guarantee registration cleanup even when lock admission itself is cancelled."""

        try:
            async with self._single_flight:
                yield
        finally:
            self._cancel_events.pop(invoke_id, None)
            self._cancelled_invokes.pop(invoke_id, None)
            self._control_tasks.pop(invoke_id, None)

    async def run(
        self,
        chunks: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
        *,
        invoke_id: str,
        tool_surface: ToolSurface,
    ) -> InvokeSettlement:
        """Consume, validate, execute, cancel if requested, and always return a control settlement."""

        if invoke_id in self._cancel_events:
            return InvokeSettlement(
                invoke_id, (ToolReceipt("", "invoke", False, "duplicate_invoke"),)
            )
        cancel_event = asyncio.Event()
        self._cancel_events[invoke_id] = cancel_event
        if invoke_id in self._cancelled_invokes:
            cancel_event.set()
            self._cancelled_invokes.pop(invoke_id, None)
        async with self._invoke_scope(invoke_id):
            parser = ToolCallAssembler()
            streamed: dict[int, int] = {}
            early_codes: dict[int, str] = {}
            streamed_receipts: list[StreamedSpeechReceipt] = []
            protocol_receipts: list[ToolReceipt] = []
            initial_names: frozenset[str] = frozenset()
            streaming_say_index: int | None = None
            stream_failed = False
            try:
                if not self.policy.validates_surface(tool_surface):
                    return InvokeSettlement(
                        invoke_id,
                        (ToolReceipt("", "tool_surface", False, "invalid_tool_surface"),),
                    )
                initial_names = tool_surface.names
                try:
                    chunk_stream = self._chunks(chunks, cancel_event)
                    try:
                        async for chunk in chunk_stream:
                            if cancel_event.is_set():
                                break
                            for clause in parser.feed(chunk):
                                if clause.index in early_codes:
                                    continue
                                if not parser.can_stream_say(clause.index, self.policy.max_calls):
                                    continue
                                if streaming_say_index is None:
                                    streaming_say_index = clause.index
                                if clause.index != streaming_say_index:
                                    continue
                                code = await self._stream_say(
                                    clause, invoke_id=invoke_id, tool_surface=tool_surface,
                                    exposed_names=initial_names, cancel_event=cancel_event,
                                )
                                if code is None:
                                    parser.accept_say_clause(clause.index, clause.text)
                                    streamed[clause.index] = (
                                        streamed.get(clause.index, 0) + len(clause.text)
                                    )
                                    streamed_receipts.append(StreamedSpeechReceipt(
                                        clause.call_id, clause.text, len(streamed_receipts)
                                    ))
                                else:
                                    early_codes[clause.index] = code
                    finally:
                        await chunk_stream.aclose()
                except Exception:
                    stream_failed = True
                    protocol_receipts.append(ToolReceipt(
                        "", "provider_stream", False, "provider_stream_error"
                    ))

                if parser.protocol_error:
                    stream_failed = True
                    if not protocol_receipts:
                        protocol_receipts.append(ToolReceipt(
                            "", "provider_stream", False, "provider_stream_error"
                        ))

                try:
                    calls = parser.finish()
                except Exception:
                    calls = ()
                    stream_failed = True
                    if not protocol_receipts:
                        protocol_receipts.append(ToolReceipt(
                            "", "provider_stream", False, "provider_stream_error"
                        ))
                if cancel_event.is_set():
                    receipts = tuple(
                        ToolReceipt(call.call_id, call.name, False, "invoke_cancelled")
                        for call in calls
                    )
                    if parser.assistant_content_seen:
                        receipts += (ToolReceipt(
                            "", "assistant_content", False, "assistant_content_discarded"
                        ),)
                    receipts += tuple(protocol_receipts)
                    return InvokeSettlement(
                        invoke_id, receipts, tuple(streamed_receipts), cancelled=True,
                        assistant_content_discarded=parser.assistant_content_seen,
                    )

                if stream_failed:
                    receipts = tuple(
                        ToolReceipt(call.call_id, call.name, False, "provider_stream_error")
                        for call in calls
                    ) + tuple(protocol_receipts)
                    if parser.assistant_content_seen:
                        receipts += (ToolReceipt(
                            "", "assistant_content", False, "assistant_content_discarded"
                        ),)
                    return InvokeSettlement(
                        invoke_id, receipts, tuple(streamed_receipts),
                        assistant_content_discarded=parser.assistant_content_seen,
                    )

                codes = self._validate_group(calls, tool_surface)
                for index, code in early_codes.items():
                    codes.setdefault(index, code)
                if any(index in codes for index in streamed):
                    for call in calls:
                        if call.index not in streamed:
                            codes.setdefault(call.index, "group_rejected")
                ordered = sorted(
                    calls,
                    key=lambda call: (self.policy.definition(call.name).effect is not EffectClass.SAY
                                      if self.policy.definition(call.name) else True, call.index),
                )
                for call in ordered:
                    if call.index in codes:
                        continue
                    definition = self.policy.definition(call.name)
                    assert definition is not None
                    fence_code = self._fence_code(
                        definition.effect, invoke_id, tool_surface, cancel_event,
                    )
                    if fence_code:
                        codes[call.index] = fence_code
                        continue
                    if definition.effect is EffectClass.SAY:
                        text = str(call.arguments["text"])
                        tails = segment_say_text(
                            text[streamed.get(call.index, 0):], final=True
                        )
                        codes[call.index] = "accepted"
                        for tail in tails:
                            tail_call = ToolCall(
                                call.call_id, call.name, {**call.arguments, "text": tail}, call.index,
                                call.raw_arguments,
                            )
                            codes[call.index] = await self._execute_control(
                                tail_call, invoke_id, cancel_event
                            )
                            if codes[call.index] != "accepted":
                                break
                            streamed_receipts.append(StreamedSpeechReceipt(
                                call.call_id, tail, len(streamed_receipts)
                            ))
                    elif definition.effect is EffectClass.SAFE_READ:
                        if call.name not in self.executors:
                            codes[call.index] = "executor_unavailable"
                        else:
                            codes[call.index] = await self._detach_observation(
                                call, invoke_id, tool_surface.input_id,
                            )
                    else:
                        codes[call.index] = await self._execute_control(call, invoke_id, cancel_event)

                receipts = [self._receipt(call, codes.get(call.index, "executor_failed")) for call in calls]
                if parser.assistant_content_seen:
                    receipts.append(ToolReceipt(
                        "", "assistant_content", False, "assistant_content_discarded"
                    ))
                receipts.extend(protocol_receipts)
                return InvokeSettlement(
                    invoke_id, tuple(receipts), tuple(streamed_receipts),
                    cancelled=cancel_event.is_set(),
                    assistant_content_discarded=parser.assistant_content_seen,
                )
            finally:
                self._cancel_events.pop(invoke_id, None)
                self._cancelled_invokes.pop(invoke_id, None)
                self._control_tasks.pop(invoke_id, None)

    async def _stream_say(
        self,
        clause: SayClause,
        *,
        invoke_id: str,
        tool_surface: ToolSurface,
        exposed_names: frozenset[str],
        cancel_event: asyncio.Event,
    ) -> str | None:
        if "say_to_user" not in exposed_names:
            return "tool_not_exposed"
        code = self._fence_code(
            EffectClass.SAY, invoke_id, tool_surface, cancel_event,
        )
        if code:
            return code
        call = ToolCall(clause.call_id, "say_to_user", {"text": clause.text}, clause.index)
        result = await self._execute_control(call, invoke_id, cancel_event)
        return None if result == "accepted" else result

    def _validate_group(self, calls: tuple[ToolCall, ...], surface: ToolSurface) -> dict[int, str]:
        codes: dict[int, str] = {}
        for position, call in enumerate(calls):
            if position >= self.policy.max_calls or call.index >= self.policy.max_calls:
                codes[call.index] = "call_limit_exceeded"
            elif call.parse_error:
                codes[call.index] = call.parse_error
            elif self.policy.definition(call.name) is None:
                codes[call.index] = "unknown_tool"
            elif call.name not in surface.names:
                codes[call.index] = "tool_not_exposed"
            elif validation := self.policy.validate_arguments(call, surface):
                codes[call.index] = validation
        eligible = [call for call in calls if call.index not in codes]
        effects = {self.policy.definition(call.name).effect for call in eligible}  # type: ignore[union-attr]
        if EffectClass.SAFE_READ in effects and EffectClass.DURABLE_ACTION in effects:
            for call in eligible:
                effect = self.policy.definition(call.name).effect  # type: ignore[union-attr]
                if effect in {EffectClass.SAFE_READ, EffectClass.DURABLE_ACTION}:
                    codes[call.index] = "illegal_effect_combination"
        durable = [call for call in eligible if self.policy.definition(call.name).effect is EffectClass.DURABLE_ACTION]  # type: ignore[union-attr]
        if len(durable) > self.policy.max_durable_calls:
            for call in durable:
                codes[call.index] = "durable_limit_exceeded"
        notes = [call for call in eligible if self.policy.definition(call.name).effect is EffectClass.NOTE]  # type: ignore[union-attr]
        if len(notes) > self.policy.max_note_calls:
            for call in notes:
                codes[call.index] = "note_limit_exceeded"
        says = [
            call for call in eligible
            if self.policy.definition(call.name).effect is EffectClass.SAY  # type: ignore[union-attr]
        ]
        for call in sorted(says, key=lambda item: item.index)[1:]:
            codes[call.index] = "say_limit_exceeded"
        return codes

    def _fence_code(
        self,
        effect: EffectClass,
        invoke_id: str,
        surface: ToolSurface,
        cancel_event: asyncio.Event,
    ) -> str | None:
        if cancel_event.is_set():
            return "invoke_cancelled"
        if self.state_provider is None:
            return "fence_unavailable"
        try:
            state = _effect_fence(self.state_provider())
        except Exception:
            return "fence_unavailable"
        if state.cancelled:
            return "invoke_cancelled"
        if state.active_invoke_id != invoke_id:
            return "stale_invoke"
        if state.input_id != surface.input_id:
            return "stale_input"
        if effect is EffectClass.SAFE_READ:
            return None
        if state.revision != surface.revision:
            return "stale_revision"
        if state.context_version != surface.context_version:
            return "stale_context_version"
        if effect in {EffectClass.SAY, EffectClass.DURABLE_ACTION}:
            response_grant = surface.response_grant
            if response_grant is None or state.response_grant != response_grant:
                return "invalid_response_grant"
            if (
                response_grant.input_id != surface.input_id
                or response_grant.revision != surface.revision
                or response_grant.context_version != surface.context_version
            ):
                return "invalid_response_grant"
            if state.speech_candidate or state.confirmed_speech:
                return "speech_fenced"
        return None

    async def _execute_control(
        self, call: ToolCall, invoke_id: str, cancel_event: asyncio.Event
    ) -> str:
        executor = self.executors.get(call.name)
        if executor is None:
            return "executor_unavailable"

        async def execute() -> None:
            result = executor(call)
            if inspect.isawaitable(result):
                await result

        task = asyncio.create_task(execute())
        self._control_tasks[invoke_id] = task
        try:
            await task
            return "accepted"
        except asyncio.CancelledError:
            cancel_event.set()
            return "invoke_cancelled"
        except ToolExecutorError as error:
            return error.code
        except Exception:
            return "executor_failed"
        finally:
            self._control_tasks.pop(invoke_id, None)

    async def _detach_observation(self, call: ToolCall, invoke_id: str, input_id: str) -> str:
        executor = self.executors.get(call.name)
        if executor is None:
            return "executor_unavailable"
        request_key = (
            input_id,
            call.name,
            json.dumps(
                dict(call.arguments), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ),
        )
        if request_key in self._observation_requests:
            self._observation_requests.move_to_end(request_key)
            return "observation_reused"
        if not self._reserve_observation_request_slot():
            return "observation_limit_reached"
        if not self.observation_limiter.try_acquire():
            return "observation_limit_reached"
        proposed_id = f"{invoke_id}:{call.call_id}"
        observation_id = proposed_id
        if self.observation_acceptance_hook is not None:
            try:
                hook_result = self.observation_acceptance_hook(proposed_id, call)
                if inspect.isawaitable(hook_result):
                    close = getattr(hook_result, "close", None)
                    if close is not None:
                        close()
                    self.observation_limiter.release()
                    return "executor_failed"
                if hook_result is not None:
                    if not isinstance(hook_result, str) or not hook_result:
                        self.observation_limiter.release()
                        return "executor_failed"
                    observation_id = hook_result
            except BaseException:
                self.observation_limiter.release()
                return "executor_failed"
        if observation_id != proposed_id:
            self._observation_requests.setdefault(request_key, observation_id)
            self._observation_requests.move_to_end(request_key)
            self._trim_observation_requests()
            self.observation_limiter.release()
            return "observation_reused"
        self._observation_requests[request_key] = observation_id
        self._observation_requests.move_to_end(request_key)
        outcome_recorded = False

        async def record_once(outcome: ObservationOutcome) -> None:
            nonlocal outcome_recorded
            if outcome_recorded:
                return
            outcome_recorded = True
            await self._record_observation_outcome(outcome)

        async def execute() -> None:
            try:
                await asyncio.sleep(0)
                result = executor(call)
                if inspect.isawaitable(result):
                    result = await result
                outcome = ObservationOutcome(
                    call.call_id, call.name, observation_id, result=result
                )
            except BaseException as exc:
                outcome = ObservationOutcome(
                    call.call_id, call.name, observation_id,
                    error_type=type(exc).__name__,
                )
            await record_once(outcome)

        coroutine = execute()
        try:
            task = asyncio.create_task(coroutine)
        except BaseException as exc:
            coroutine.close()
            try:
                await record_once(ObservationOutcome(
                    call.call_id, call.name, observation_id,
                    error_type=type(exc).__name__,
                ))
            finally:
                self.observation_limiter.release()
            return "executor_failed"
        self._observation_tasks.add(task)
        self._active_observation_requests.add(request_key)
        self._observation_task_keys[task] = request_key
        task.add_done_callback(self._observation_done)
        await asyncio.sleep(0)
        if task.cancelled():
            await record_once(ObservationOutcome(
                call.call_id, call.name, observation_id, error_type="CancelledError",
            ))
            return "executor_failed"
        return "detached"

    def _observation_done(self, task: asyncio.Task[Any]) -> None:
        """Release admission even when cancellation happens before the executor coroutine starts."""

        self._observation_tasks.discard(task)
        request_key = self._observation_task_keys.pop(task, None)
        if request_key is not None:
            self._active_observation_requests.discard(request_key)
        self._trim_observation_requests()
        self.observation_limiter.release()

    def _reserve_observation_request_slot(self) -> bool:
        """Make one bounded slot by evicting only a terminal request identity."""

        self._trim_observation_requests(target=_OBSERVATION_HISTORY_LIMIT - 1)
        return len(self._observation_requests) < _OBSERVATION_HISTORY_LIMIT

    def _trim_observation_requests(
        self, *, target: int = _OBSERVATION_HISTORY_LIMIT
    ) -> None:
        while len(self._observation_requests) > target:
            terminal = next(
                (
                    key for key in self._observation_requests
                    if key not in self._active_observation_requests
                ),
                None,
            )
            if terminal is None:
                return
            self._observation_requests.pop(terminal, None)

    async def _record_observation_outcome(self, outcome: ObservationOutcome) -> None:
        """Retain terminal truth and contain completion-hook failures without skipping release."""

        self._observation_outcomes.append(outcome)
        if self.observation_completion_hook is None:
            return
        try:
            hook_result = self.observation_completion_hook(outcome)
            if inspect.isawaitable(hook_result):
                await hook_result
        except BaseException:
            pass

    @staticmethod
    def _receipt(call: ToolCall, code: str) -> ToolReceipt:
        return ToolReceipt(
            call.call_id,
            call.name,
            code in {"accepted", "detached", "observation_reused"},
            code,
        )

    @staticmethod
    async def _chunks(
        chunks: Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]],
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[Mapping[str, Any]]:
        if hasattr(chunks, "__aiter__"):
            iterator = chunks.__aiter__()  # type: ignore[union-attr]
            try:
                while not cancel_event.is_set():
                    next_task = asyncio.create_task(anext(iterator))
                    cancel_task = asyncio.create_task(cancel_event.wait())
                    try:
                        done, _ = await asyncio.wait(
                            (next_task, cancel_task), return_when=asyncio.FIRST_COMPLETED,
                        )
                        if cancel_task in done:
                            break
                        try:
                            chunk = next_task.result()
                        except StopAsyncIteration:
                            break
                    finally:
                        next_task.cancel()
                        cancel_task.cancel()
                        await asyncio.gather(next_task, cancel_task, return_exceptions=True)
                    yield chunk
            finally:
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    result = close()
                    if inspect.isawaitable(result):
                        await result
            return
        for chunk in chunks:  # type: ignore[union-attr]
            if cancel_event.is_set():
                break
            yield chunk


def _effect_fence(value: EffectFence | Mapping[str, Any]) -> EffectFence:
    if isinstance(value, EffectFence):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("state provider must return EffectFence or a mapping")
    return EffectFence(
        active_invoke_id=str(value["active_invoke_id"]), input_id=str(value["input_id"]),
        revision=int(value["revision"]), context_version=int(value["context_version"]),
        response_grant=value.get("response_grant"),
        speech_candidate=bool(value.get("speech_candidate", False)),
        confirmed_speech=bool(value.get("confirmed_speech", False)),
        cancelled=bool(value.get("cancelled", False)),
    )


__all__ = ["ObservationLimiter", "ToolHarness"]
