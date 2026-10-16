"""Runs one async Main Agent tool stream through parsing, policy, fencing, and concrete executors.

The Harness owns control-invoke single-flight and streams an authorized ``say_to_user`` before staging other
effects. Safe reads detach under one three-slot asyncio semaphore; Runtime's acceptance/completion hooks own
dedupe and canonical Observation state. Executors and provider chunks use the single production async shape.
Frozen debug scopes measure decoded SAY text, eligible clauses and effect authorization on the same
streaming path, without retaining model payloads or adding tasks.
SAY segmentation shares sentence/unit rules with the parser; text closure releases the short tail
before unrelated provider work, while envelope validation still governs final receipts and cancellation.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, Callable, Mapping
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
from .stream import SayClause, ToolCallAssembler, clause_flush_reason, segment_say_text, speech_units


Executor = Callable[[ToolCall], Any]


class ToolHarness:
    """Coordinate one async provider stream while keeping detached reads outside control settlement."""

    def __init__(
        self,
        policy: ToolSurfacePolicy,
        executors: Mapping[str, Executor],
        *,
        state_provider: Callable[[], EffectFence | Mapping[str, Any]] | None = None,
        observation_acceptance_hook: Callable[[str, ToolCall], str] | None = None,
        observation_completion_hook: Callable[[ObservationOutcome], Any] | None = None,
        observation_semaphore: asyncio.Semaphore | None = None,
        say_closed_hook: Callable[[dict[str, Any]], Any] | None = None,
        diagnostic_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.say_closed_hook = say_closed_hook
        self.diagnostic_hook = diagnostic_hook
        self.policy = policy
        self.executors = dict(executors)
        self.state_provider = state_provider
        self.observation_acceptance_hook = observation_acceptance_hook
        self.observation_completion_hook = observation_completion_hook
        self.observation_semaphore = observation_semaphore or asyncio.Semaphore(3)
        self._single_flight = asyncio.Lock()
        self._cancel: dict[str, asyncio.Event] = {}
        self._control_tasks: dict[str, asyncio.Task[Any]] = {}
        self._observations: set[asyncio.Task[None]] = set()
        self._outcomes: list[ObservationOutcome] = []

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
        event = self._cancel.get(invoke_id)
        if event:
            event.set()
        task = self._control_tasks.get(invoke_id)
        if task:
            task.cancel()

    async def wait_for_observations(self) -> tuple[ObservationOutcome, ...]:
        if self._observations:
            await asyncio.gather(*tuple(self._observations), return_exceptions=True)
        return tuple(self._outcomes)

    async def cancel_observations(self) -> tuple[ObservationOutcome, ...]:
        for task in tuple(self._observations):
            task.cancel()
        return await self.wait_for_observations()

    async def run(
        self,
        chunks: AsyncIterable[Mapping[str, Any]],
        *,
        invoke_id: str,
        tool_surface: ToolSurface,
        debug: Any = None,
    ) -> InvokeSettlement:
        if invoke_id in self._cancel:
            return InvokeSettlement(invoke_id, (ToolReceipt("", "invoke", False, "duplicate_invoke"),))
        event = asyncio.Event()
        self._cancel[invoke_id] = event
        async with self._single_flight:
            try:
                return await self._consume(chunks, invoke_id, tool_surface, event, debug)
            finally:
                self._cancel.pop(invoke_id, None)
                self._control_tasks.pop(invoke_id, None)

    async def _consume(
        self,
        chunks: AsyncIterable[Mapping[str, Any]],
        invoke_id: str,
        surface: ToolSurface,
        event: asyncio.Event,
        debug: Any = None,
    ) -> InvokeSettlement:
        parser = ToolCallAssembler()
        streamed: dict[int, int] = {}
        streamed_receipts: list[StreamedSpeechReceipt] = []
        early_codes: dict[int, str] = {}
        frozen: dict[int, dict[str, str]] = {}
        counts: dict[int, int] = {}
        closed: set[int] = set()
        cancelled_scopes: set[int] = set()
        first_text: set[int] = set()
        first_tool = True
        started = time.monotonic()

        def trace(name: str, **fields: Any) -> None:
            if debug is not None and debug.enabled:
                debug.emit("model", name, invokeId=invoke_id, inputId=surface.input_id,
                    elapsedMs=(time.monotonic() - started) * 1000, **fields)

        async def close_say(call_id: str, index: int, cancelled: bool = False) -> None:
            if index not in counts or index in cancelled_scopes or (index in closed and not cancelled):
                return
            closed.add(index)
            if cancelled:
                cancelled_scopes.add(index)
            if self.say_closed_hook is not None:
                try:
                    await self.say_closed_hook({"say_id": f"{invoke_id}:{call_id}", "invoke_id": invoke_id,
                        "call_id": call_id, "clause_count": counts[index], "cancelled": cancelled,
                        "profile_id": surface.avatar_capabilities.profile_id,
                        "config_revision": surface.avatar_capabilities.revision})
                except Exception:
                    pass  # Visual notification failure never invalidates delivered text.

        async def deliver(clause: SayClause) -> str | None:
            avatar = frozen.get(clause.index, surface.avatar_capabilities.validate(clause.avatar))
            if self.diagnostic_hook is not None and clause.index not in frozen and clause.avatar is not None and clause.avatar != avatar:
                self.diagnostic_hook("main.avatar.invalid")
            reason = clause_flush_reason(clause.text, first=not streamed.get(clause.index))
            trace("say.clause_ready", sayId=f"{invoke_id}:{clause.call_id}",
                charCount=len(clause.text), count=speech_units(clause.text), flushReason=reason)
            code = await self._stream_say(clause, invoke_id, surface, event, avatar, trace)
            if code is None:
                frozen.setdefault(clause.index, avatar)
                counts[clause.index] = counts.get(clause.index, 0) + 1
                streamed[clause.index] = streamed.get(clause.index, 0) + len(clause.text)
                streamed_receipts.append(StreamedSpeechReceipt(clause.call_id, clause.text, len(streamed_receipts)))
            return code

        try:
            async for chunk in _until_cancelled(chunks, event):
                clauses = parser.feed(chunk)
                if parser.calls and first_tool:
                    trace("first_tool_delta")
                    first_tool = False
                for index, partial in parser.calls.items():
                    stream = partial.say_stream
                    if stream is not None and stream.decoded and not stream.invalid and index not in first_text:
                        first_text.add(index)
                        trace("say.first_text", sayId=f"{invoke_id}:{partial.call_id}", charCount=len(stream.decoded))
                for clause in clauses:
                    if clause.index in early_codes or not parser.can_stream_say(clause.index, self.policy.max_calls):
                        continue
                    if any(index != clause.index for index in streamed):
                        early_codes[clause.index] = "say_limit_exceeded"
                        continue
                    code = await deliver(clause)
                    if code:
                        early_codes[clause.index] = code
                        continue
                    parser.accept_say_clause(clause.index, clause.text)
                # Close each complete SAY immediately; a later slow tool must not own its lifetime.
                for call in parser.finish():
                    stream = parser.say_stream(call.index)
                    if stream is None or not stream.object_closed or call.index in closed or call.index in early_codes:
                        continue
                    if not parser.can_stream_say(call.index, self.policy.max_calls) or any(index != call.index for index in streamed):
                        continue
                    if code := self.policy.validate_arguments(call, surface):
                        early_codes[call.index] = code
                        await close_say(call.call_id, call.index, True)
                        continue
                    for text in segment_say_text(str(call.arguments["text"])[streamed.get(call.index, 0):], final=True,
                                                 first=not streamed.get(call.index)):
                        code = await deliver(SayClause(call.index, call.call_id, text, stream.avatar))
                        if code:
                            early_codes[call.index] = code
                            break
                    if self.diagnostic_hook is not None and call.index in frozen and surface.avatar_capabilities.validate(stream.avatar) != frozen[call.index]:
                        self.diagnostic_hook("main.avatar.late")
                    await close_say(call.call_id, call.index, call.index in early_codes)
        except asyncio.CancelledError:
            event.set()
            for call in parser.finish():
                await close_say(call.call_id, call.index, True)
            raise
        except Exception:
            parser.protocol_error = "provider_stream_error"

        calls = parser.finish()
        if event.is_set():
            for call in calls:
                await close_say(call.call_id, call.index, True)
            return InvokeSettlement(
                invoke_id,
                tuple(ToolReceipt(call.call_id, call.name, False, "invoke_cancelled") for call in calls),
                tuple(streamed_receipts),
                cancelled=True,
                assistant_content_discarded=parser.assistant_content_seen,
            )
        if parser.protocol_error:
            for call in calls:
                await close_say(call.call_id, call.index, True)
            return InvokeSettlement(
                invoke_id,
                tuple(ToolReceipt(call.call_id, call.name, False, "provider_stream_error") for call in calls)
                or (ToolReceipt("", "provider_stream", False, "provider_stream_error"),),
                tuple(streamed_receipts),
                assistant_content_discarded=parser.assistant_content_seen,
            )

        codes = self._validate_group(calls, surface)
        codes.update({index: code for index, code in early_codes.items() if index not in codes})
        if any(index in codes for index in streamed):
            for call in calls:
                if call.index not in streamed:
                    codes.setdefault(call.index, "group_rejected")
        ordered = sorted(
            calls,
            key=lambda call: (
                self.policy.definition(call.name).effect is not EffectClass.SAY
                if self.policy.definition(call.name) else True,
                call.index,
            ),
        )
        for call in ordered:
            if call.index in codes:
                continue
            definition = self.policy.definition(call.name)
            assert definition is not None
            if code := self._fence_code(definition.effect, invoke_id, surface, event):
                codes[call.index] = code
            elif definition.effect is EffectClass.SAY:
                codes[call.index] = "accepted"
                for text in segment_say_text(str(call.arguments["text"])[streamed.get(call.index, 0):], final=True,
                                             first=not streamed.get(call.index)):
                    code = await deliver(SayClause(call.index, call.call_id, text, call.arguments.get("avatar")))
                    codes[call.index] = code or "accepted"
                    if code:
                        break
                await close_say(call.call_id, call.index, codes[call.index] != "accepted")
            elif definition.effect is EffectClass.SAFE_READ:
                codes[call.index] = self._detach(call, invoke_id)
            else:
                codes[call.index] = await self._execute(call, invoke_id, event)
        for call in calls:
            await close_say(call.call_id, call.index, codes.get(call.index) != "accepted")
        receipts = [self._receipt(call, codes.get(call.index, "executor_failed")) for call in calls]
        if parser.assistant_content_seen:
            receipts.append(ToolReceipt("", "assistant_content", False, "assistant_content_discarded"))
        return InvokeSettlement(
            invoke_id,
            tuple(receipts),
            tuple(streamed_receipts),
            assistant_content_discarded=parser.assistant_content_seen,
        )

    async def _stream_say(
        self, clause: SayClause, invoke_id: str, surface: ToolSurface, event: asyncio.Event, avatar: Mapping[str, str],
        trace: Callable[..., None],
    ) -> str | None:
        if "say_to_user" not in surface.names:
            return "tool_not_exposed"
        if code := self._fence_code(EffectClass.SAY, invoke_id, surface, event):
            return code
        trace("say.authorized", sayId=f"{invoke_id}:{clause.call_id}", charCount=len(clause.text))
        result = await self._execute(
            ToolCall(clause.call_id, "say_to_user", {"text": clause.text}, clause.index, avatar=avatar),
            invoke_id,
            event,
        )
        return None if result == "accepted" else result

    def _validate_group(self, calls: tuple[ToolCall, ...], surface: ToolSurface) -> dict[int, str]:
        codes: dict[int, str] = {}
        for position, call in enumerate(calls):
            if position >= self.policy.max_calls or call.index >= self.policy.max_calls:
                codes[call.index] = "call_limit_exceeded"
            elif self.policy.definition(call.name) is None:
                codes[call.index] = "unknown_tool"
            elif call.name not in surface.names:
                codes[call.index] = "tool_not_exposed"
            elif code := self.policy.validate_arguments(call, surface):
                codes[call.index] = code
        eligible = [call for call in calls if call.index not in codes]
        effects = {self.policy.definition(call.name).effect for call in eligible}  # type: ignore[union-attr]
        if EffectClass.SAFE_READ in effects and EffectClass.DURABLE_ACTION in effects:
            for call in eligible:
                if self.policy.definition(call.name).effect in {EffectClass.SAFE_READ, EffectClass.DURABLE_ACTION}:  # type: ignore[union-attr]
                    codes[call.index] = "illegal_effect_combination"
        for effect, maximum, code in (
            (EffectClass.DURABLE_ACTION, self.policy.max_durable_calls, "durable_limit_exceeded"),
            (EffectClass.NOTE, self.policy.max_note_calls, "note_limit_exceeded"),
            (EffectClass.SAY, 1, "say_limit_exceeded"),
        ):
            matches = [call for call in eligible if self.policy.definition(call.name).effect is effect]  # type: ignore[union-attr]
            for call in matches[maximum:]:
                codes[call.index] = code
        return codes

    def _fence_code(
        self, effect: EffectClass, invoke_id: str, surface: ToolSurface, event: asyncio.Event
    ) -> str | None:
        if event.is_set():
            return "invoke_cancelled"
        if self.state_provider is None:
            return "fence_unavailable"
        try:
            state = _fence(self.state_provider())
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
            if surface.response_grant is None or state.response_grant != surface.response_grant:
                return "invalid_response_grant"
            if state.speech_candidate or state.confirmed_speech:
                return "speech_fenced"
        return None

    async def _execute(self, call: ToolCall, invoke_id: str, event: asyncio.Event) -> str:
        executor = self.executors.get(call.name)
        if executor is None:
            return "executor_unavailable"
        task = asyncio.create_task(executor(call))
        self._control_tasks[invoke_id] = task
        try:
            await task
            return "accepted"
        except asyncio.CancelledError:
            event.set()
            return "invoke_cancelled"
        except ToolExecutorError as error:
            return error.code
        except Exception:
            return "executor_failed"
        finally:
            self._control_tasks.pop(invoke_id, None)

    def _detach(self, call: ToolCall, invoke_id: str) -> str:
        executor = self.executors.get(call.name)
        if executor is None or self.observation_acceptance_hook is None:
            return "executor_unavailable"
        proposed = f"{invoke_id}:{call.call_id}"
        try:
            observation_id = self.observation_acceptance_hook(proposed, call)
        except Exception:
            return "executor_failed"
        if observation_id != proposed:
            return "observation_reused"

        async def run() -> None:
            try:
                async with self.observation_semaphore:
                    result = await executor(call)
                outcome = ObservationOutcome(call.call_id, call.name, observation_id, result=result)
            except BaseException as error:
                outcome = ObservationOutcome(
                    call.call_id, call.name, observation_id, error_type=type(error).__name__,
                )
            self._outcomes.append(outcome)
            if self.observation_completion_hook is not None:
                try:
                    result = self.observation_completion_hook(outcome)
                    if asyncio.iscoroutine(result):
                        await result
                except BaseException:
                    pass

        task = asyncio.create_task(run(), name="main-agent-observation")
        self._observations.add(task)
        task.add_done_callback(self._observations.discard)
        return "detached"

    @staticmethod
    def _receipt(call: ToolCall, code: str) -> ToolReceipt:
        return ToolReceipt(call.call_id, call.name, code in {"accepted", "detached", "observation_reused"}, code)


def _fence(value: EffectFence | Mapping[str, Any]) -> EffectFence:
    if isinstance(value, EffectFence):
        return value
    return EffectFence(
        active_invoke_id=str(value["active_invoke_id"]),
        input_id=str(value["input_id"]),
        revision=int(value["revision"]),
        context_version=int(value["context_version"]),
        response_grant=value.get("response_grant"),
        speech_candidate=bool(value.get("speech_candidate", False)),
        confirmed_speech=bool(value.get("confirmed_speech", False)),
        cancelled=bool(value.get("cancelled", False)),
    )


async def _until_cancelled(
    chunks: AsyncIterable[Mapping[str, Any]], event: asyncio.Event,
):
    """Stop a blocked provider stream promptly when Runtime revokes its invoke."""

    iterator = chunks.__aiter__()
    try:
        while not event.is_set():
            next_chunk = asyncio.create_task(anext(iterator))
            cancelled = asyncio.create_task(event.wait())
            done, _pending = await asyncio.wait(
                (next_chunk, cancelled), return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done and event.is_set():
                next_chunk.cancel()
                await asyncio.gather(next_chunk, return_exceptions=True)
                return
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            try:
                yield next_chunk.result()
            except StopAsyncIteration:
                return
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            await close()


__all__ = ["ToolHarness"]
