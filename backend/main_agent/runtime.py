"""Own one Profile's causal input, control invokes, tools, speech delivery, and Memory scheduling.

MainAgentRuntime folds transcript/VAD/EOU facts into one ActiveInput, coalesces control triggers into a
single-flight drain, freezes Snapshot and ToolSurface together, and fences every effect. Renderer text enters
Context only after complete acknowledgement; Observation work remains detached, while TaskOperations
owns proposal timers, accepted controls and Task fact synchronization. Memory uses two owners only: a user-activity-resettable idle timer seals a canonical
Context boundary and immediately retires its causal input, then an independent archive worker submits that
sealed boundary without blocking or being cancelled by later input. Context cleanup occurs only after Memory
success and never retires the newer causal scope.
Frozen Hub diagnostic scopes record opt-in control timing and content-free tool receipts.
Harness settlements retain content-free receipt counters for backend diagnostics. No say_to_user means
intentional silence; tool rejections and discarded ordinary content do not become frontend debug errors.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .context import ClauseState, ContextStore, PendingConversationArchive
from .observations import ObservationLedger
from .snapshot import Snapshot, SnapshotProjector
from .tasks import TaskBridge
from .task_operations import TaskOperations, PendingTaskProposal, TaskProposalStatus
from .tool_runtime import (
    EffectFence,
    InvokeMode,
    InvokeSettlement,
    ObservationOutcome,
    ResponseGrant,
    ToolCall,
    ToolExecutorError,
    ToolHarness,
    ToolSurface,
    ToolSurfacePolicy,
)


ProviderPort = Callable[["InvokeRequest"], AsyncIterable[Mapping[str, Any]]]
RendererPort = Callable[["RendererClause"], Awaitable[None]]
Executor = Callable[[ToolCall], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class InputSegment:
    """One stable utterance identity whose STT snapshots replace text in place."""

    segment_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ActiveInput:
    """The current causal scope, ordered utterances, and their composed transcript."""

    input_id: str
    revision: int
    latest_transcript: str
    segments: tuple[InputSegment, ...] = ()


@dataclass(frozen=True, slots=True)
class PendingTrigger:
    """One coalesced request for the next control invoke, never an item in a FIFO queue."""

    input_id: str
    mode: InvokeMode
    reason: str


@dataclass(frozen=True, slots=True)
class PrepareAdmissionPolicy:
    """Configurable rate, growth, wait, punctuation, and per-input PREPARE guards."""

    min_interval_seconds: float = 1.5
    min_growth_units: int = 12
    max_wait_seconds: float = 5.0
    punctuation_growth_units: int = 4
    max_prepares_per_input: int = 2

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0 or self.max_wait_seconds < 0:
            raise ValueError("PREPARE timing limits must be non-negative")
        if self.min_growth_units < 1 or self.punctuation_growth_units < 1:
            raise ValueError("PREPARE growth limits must be positive")
        if self.max_prepares_per_input < 1:
            raise ValueError("PREPARE input budget must be positive")


@dataclass(frozen=True, slots=True)
class RendererClause:
    """One short user-visible text offer carrying the coordinates that authorized it."""

    clause_id: str
    text: str
    invoke_id: str
    input_id: str
    revision: int
    profile_id: str = ""
    generation: int = 0
    config_revision: int = 0
    say_id: str = ""
    call_id: str = ""
    avatar: Mapping[str, str] | None = None


@dataclass(slots=True)
class InvokeRequest:
    """Provider-facing immutable facts plus the terminal harness settlement filled after the run."""

    invoke_id: str
    input_id: str
    revision: int
    context_version: int
    mode: InvokeMode
    reason: str
    response_grant: ResponseGrant | None
    snapshot: Snapshot
    tool_surface: ToolSurface
    settlement: InvokeSettlement | None = None
    debug_scope: Any = None


@dataclass(slots=True)
class _ActiveInvoke:
    request: InvokeRequest
    settled: asyncio.Event


_TRIGGER_PRIORITY = {
    InvokeMode.PREPARE: 0,
    InvokeMode.CONTINUE: 1,
    InvokeMode.RESPOND: 2,
}
_PUNCTUATED = re.compile(r"[。！？!?；;.]\s*$")
_ENGLISH_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")


class MainAgentRuntime:
    """Event-driven owner of one profile's latest input, invoke drain, and response capability."""

    def __init__(
        self,
        *,
        context_store: ContextStore,
        snapshot_projector: SnapshotProjector,
        tool_policy: ToolSurfacePolicy,
        provider: ProviderPort,
        renderer: RendererPort,
        executors: Mapping[str, Executor],
        soul: str,
        memory_service: Any | None = None,
        memory_idle_seconds: float = 300.0,
        memory_recall_timeout_seconds: float = 0.4,
        wall_clock: Callable[[], float] | None = None,
        clock: Callable[[], float] | None = None,
        prepare_admission: PrepareAdmissionPolicy | None = None,
        observation_ledger: ObservationLedger | None = None,
        observation_semaphore: asyncio.Semaphore | None = None,
        proposal_sleep: Callable[[float], Awaitable[None]] | None = None,
        task_manager: Any | None = None,
        owner_profile_id: str | None = None,
        metrics: Any | None = None,
    ) -> None:
        if not callable(provider) or not callable(renderer):
            raise TypeError("provider and renderer ports must be callable")
        if not isinstance(soul, str):
            raise TypeError("soul must be text")
        self.context_store = context_store
        self.snapshot_projector = snapshot_projector
        self.tool_policy = tool_policy
        self.provider = provider
        self.renderer = renderer
        self.soul = soul
        if memory_idle_seconds <= 0:
            raise ValueError("memory_idle_seconds must be positive")
        if memory_recall_timeout_seconds <= 0:
            raise ValueError("memory_recall_timeout_seconds must be positive")
        if memory_service is not None and owner_profile_id is None:
            raise ValueError("MemoryService requires owner_profile_id")
        self.memory_service = memory_service
        self.memory_idle_seconds = float(memory_idle_seconds)
        self.memory_recall_timeout_seconds = float(memory_recall_timeout_seconds)
        self.wall_clock = wall_clock or time.time
        self.owner_profile_id = owner_profile_id
        self.metrics = metrics
        self.debug = None
        self.clock = clock or time.monotonic
        self.prepare_admission = prepare_admission or PrepareAdmissionPolicy()
        self.observation_ledger = observation_ledger or ObservationLedger()
        if task_manager is not None and owner_profile_id is None:
            raise ValueError("task_manager requires owner_profile_id")
        self._task_bridge = TaskBridge(task_manager, owner_profile_id) if task_manager is not None else None

        self._lock = asyncio.Lock()
        self._active_input: ActiveInput | None = None
        self._has_external_commit = False
        self._latest_context_version = 0
        self._response_grant: ResponseGrant | None = None
        self._pending_trigger: PendingTrigger | None = None
        self._active_invoke: _ActiveInvoke | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._drain_scheduled = False
        self._barrier_intent: object | None = None
        self._control_generation = 0
        self._preinvoke_mode: InvokeMode | None = None
        self._preinvoke_settled: asyncio.Event | None = None
        self._shutdown = False
        self._user_speaking = False
        self._speech_candidate = False
        self._confirmed_speech = False
        self._prepare_count = 0
        self._last_prepare_at: float | None = None
        self._last_prepare_units = 0
        self._prepare_wait_started_at: float | None = None
        self._clause_origins: dict[str, tuple[str, int]] = {}
        self._observation_origins: dict[str, tuple[str, int]] = {}
        self._task_operations = TaskOperations(
            self._task_bridge, self._lock, self._accept_task_fact_locked, self._task_error,
            self.clock, proposal_sleep or asyncio.sleep,
        ) if self._task_bridge is not None else None
        self._memory_idle_task: asyncio.Task[None] | None = None
        self._memory_archive_task: asyncio.Task[None] | None = None
        self.accepting = True
        self.user_message_listener = None
        self.state_listener = None
        self.error_listener = None
        self.say_closed_listener = None
        self.generation = 0
        self._published_segments: set[str] = set()
        self._speech_settled = asyncio.Event()
        self._speech_settled.set()

        runtime_executors = dict(executors)
        for task_tool in (
            "task_assign", "inspect_tasks", "answer_task_question",
            "pause_task", "resume_task", "cancel_task", "delete_task", "rerun_task",
        ):
            runtime_executors.pop(task_tool, None)
        runtime_executors["say_to_user"] = self._offer_say
        runtime_executors["note_think_checkpoint"] = self._save_think_note
        if self._task_bridge is not None:
            runtime_executors.update({
                "task_assign": self._assign_task,
                "inspect_tasks": self._task_bridge.inspect_tasks,
                "answer_task_question": self._task_operations.execute_control,
                "pause_task": self._task_operations.execute_control,
                "resume_task": self._task_operations.execute_control,
                "cancel_task": self._task_operations.execute_control,
                "delete_task": self._task_operations.execute_control,
                "rerun_task": self._task_operations.execute_control,
            })
        self.harness = ToolHarness(
            tool_policy,
            runtime_executors,
            state_provider=self._effect_fence,
            observation_acceptance_hook=self._observation_accepted,
            observation_completion_hook=self._observation_completed,
            say_closed_hook=self._say_closed,
            diagnostic_hook=self._increment_metric,
            observation_semaphore=observation_semaphore,
        )

    @property
    def active_input(self) -> ActiveInput | None:
        return self._active_input

    def debug_snapshot(self) -> None:
        debug = self.debug
        if debug is not None and debug.enabled:
            active = self._active_input
            invoke = self._active_invoke
            pending = self._pending_trigger
            debug.recorder.update_state(debug, "model", inputId=active.input_id if active else None,
                revision=active.revision if active else None,
                invokeId=invoke.request.invoke_id if invoke else None, running=invoke is not None,
                mode=invoke.request.mode.value if invoke else None,
                pending=pending.mode.value if pending else None, prepareCount=self._prepare_count,
                speechCandidate=self._speech_candidate, confirmedSpeech=self._confirmed_speech)

    def _debug(self, name: str, **details: Any) -> None:
        debug = self.debug
        if debug is not None and debug.enabled:
            active = self._active_input
            debug.emit("model", name, inputId=active.input_id if active else None, **details)

    @property
    def latest_context_version(self) -> int:
        return self._latest_context_version

    @property
    def current_response_grant(self) -> ResponseGrant | None:
        return self._response_grant

    @property
    def pending_trigger(self) -> PendingTrigger | None:
        return self._pending_trigger

    @property
    def task_proposals(self) -> tuple[PendingTaskProposal, ...]:
        """Return immutable proposal views in acceptance order for diagnostics and UI projection."""

        return self._task_operations.proposals if self._task_operations is not None else ()

    def restart_busy(self) -> bool:
        """Read active work while the restart route holds this runtime's admission lock."""
        return bool(self._active_invoke or self._pending_trigger or self._barrier_intent or self._preinvoke_settled
                    or self._user_speaking or self._speech_candidate or self._confirmed_speech
                    or self.context_store.has_unsettled_clauses()
                    or (self._task_operations is not None and self._task_operations.busy))

    def fence_admission(self) -> None:
        self.accepting = False
        self._cancel_memory_idle_task_locked()
        self._cancel_pending_proposals_locked("backend_restart")

    async def _publish_user_segments(self, source: str) -> None:
        if self.user_message_listener is None or self._active_input is None:
            return
        for segment in self._active_input.segments:
            if segment.segment_id not in self._published_segments:
                await self.user_message_listener(segment.segment_id, segment.text, source)
                self._published_segments.add(segment.segment_id)

    async def startup(self) -> None:
        """Start Task events and resume any persisted Profile conversation idle deadline."""

        async with self._lock:
            self.context_store.recover_interrupted_clauses()
            self._refresh_speech_settled_locked()
            self._resume_memory_idle_locked()
        if self._task_operations is not None:
            await self._task_operations.startup()
        self._increment_metric("main.runtime.startup")

    async def update_soul(self, soul: str) -> None:
        """Apply Electron's effective Soul to snapshots created after this serialized update."""

        effective = _required_text(soul, "soul")
        async with self._lock:
            self.soul = effective

    async def wait_for_task_proposals(self) -> None:
        if self._task_operations is not None:
            await self._task_operations.wait_for_proposals()

    async def cancel_task_proposal(self, proposal_id: str) -> bool:
        async with self._lock:
            return self._task_operations.cancel(proposal_id) if self._task_operations is not None else False

    async def update_transcript(
        self, text: str, *, segment_id: str | None = None, request_prepare: bool = True
    ) -> bool:
        """Update one stable utterance, advance revision, and optionally checkpoint."""

        transcript = _required_text(text, "transcript")
        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._cancel_memory_idle_task_locked()
            self._cancel_pending_proposals_locked("confirmed_speech")
            self._user_speaking = True
            self._speech_candidate = False
            self._confirmed_speech = True
            identifier = self._segment_id_locked(segment_id)
            if (
                self._active_input is not None
                and any(item.segment_id == identifier and item.text == transcript for item in self._active_input.segments)
            ):
                return False
            self._advance_input_locked(transcript, identifier)
            admitted = request_prepare and self._request_prepare_locked("stable_transcript", False)
            self._kick_locked()
            return bool(admitted)

    async def submit_text(self, text: str, *, segment_id: str | None = None) -> ResponseGrant:
        """Accept an explicit silent-text submission and grant exactly its current RESPOND coordinates."""

        transcript = _required_text(text, "text submission")
        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._cancel_memory_idle_task_locked()
            barrier = self._prepare_barrier_locked()
            self._advance_input_locked(transcript, segment_id or self._new_id("segment"))
            assert self._active_input is not None
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = False
            grant = self._create_grant_locked()
            await self._publish_user_segments("text")
            self._merge_trigger_locked(InvokeMode.RESPOND, "explicit_text_submit")
            self._kick_locked()
            self._start_memory_idle_locked()
        if barrier is not None:
            await barrier.wait()
        return grant

    async def vad_start(self) -> None:
        """Fence response effects immediately for an acoustic speech candidate without changing revision."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._cancel_memory_idle_task_locked()
            self._user_speaking = True
            self._speech_candidate = True
            self._confirmed_speech = False
            self._freeze_pending_proposals_locked()

    async def confirm_speech(self) -> None:
        """Confirm barge-in, revoke response authority, and cancel current control/speech ownership."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._cancel_memory_idle_task_locked()
            self._user_speaking = True
            self._speech_candidate = False
            self._confirmed_speech = True
            self._cancel_pending_proposals_locked("confirmed_speech")
            self._response_grant = None
            self._pending_trigger = None
            if self._active_invoke is not None:
                self.harness.cancel(self._active_invoke.request.invoke_id)
            for clause_id in tuple(self._clause_origins):
                clause = self.context_store.get_clause(clause_id)
                if clause.state in {
                    ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING,
                }:
                    self.context_store.ack_clause(clause_id, complete=False)
                self._clause_origins.pop(clause_id, None)
            self._refresh_speech_settled_locked()

    async def vad_stop_candidate(self) -> None:
        """Record acoustic silence only; this event never grants or schedules RESPOND."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._user_speaking = False
            self._speech_candidate = True

    async def reject_speech_candidate(self) -> None:
        """Resume a still-valid prior response capability after a VAD candidate is classified as noise."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = False
            self._resume_pending_proposals_locked()
            self._resume_memory_idle_locked()

    async def eou_incomplete(self) -> bool:
        """Settle response-class work, revoke its grant, then admit only high-priority PREPARE."""

        intent = object()
        committed = False
        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            self._barrier_intent = intent
            self._control_generation += 1
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = True
            self._cancel_pending_proposals_locked("confirmed_speech")
            self._response_grant = None
            self._pending_trigger = None
            active = self._active_invoke
            if active is not None and active.request.mode in {
                InvokeMode.RESPOND, InvokeMode.CONTINUE,
            }:
                self.harness.cancel(active.request.invoke_id)
                barrier = active.settled
            else:
                barrier = None
        try:
            if barrier is not None:
                await barrier.wait()
            await self._lock.acquire()
            try:
                if self._barrier_intent is not intent:
                    return False
                self._response_grant = None
                self._pending_trigger = None
                admitted = self._request_prepare_locked("eou_incomplete", True)
                self._barrier_intent = None
                self._kick_locked()
                committed = True
                return admitted
            finally:
                self._lock.release()
        finally:
            if not committed:
                await asyncio.shield(self._release_barrier_intent(intent))

    async def eou_complete(self) -> ResponseGrant:
        """Apply the StrongPause barrier for a complete local EOU decision."""

        return await self.strong_pause("eou_complete")

    async def endpoint_fallback(self) -> ResponseGrant:
        """Apply the same StrongPause barrier after the bounded EOU/VAD fallback deadline."""

        return await self.strong_pause("endpoint_fallback")

    async def strong_pause(self, reason: str) -> ResponseGrant:
        """Settle PREPARE, then grant RESPOND against the latest post-barrier coordinates."""

        normalized_reason = _required_text(reason, "reason")
        intent = object()
        committed = False
        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            if self._active_input is None:
                raise RuntimeError("StrongPause requires an active input")
            if self._speech_candidate or self._confirmed_speech:
                self._cancel_pending_proposals_locked("confirmed_speech")
            self._barrier_intent = intent
            self._control_generation += 1
            self._pending_trigger = None
            barrier = self._prepare_barrier_locked()
        try:
            if barrier is not None:
                await barrier.wait()
            await self._lock.acquire()
            try:
                if self._barrier_intent is not intent:
                    raise RuntimeError("StrongPause was superseded by a newer endpoint decision")
                if self._active_input is None:
                    raise RuntimeError("StrongPause lost its active input")
                if not self.accepting:
                    raise RuntimeError("backend_restarting")
                await self._publish_user_segments("voice")
                self._user_speaking = False
                self._speech_candidate = False
                self._confirmed_speech = False
                grant = self._create_grant_locked()
                self._pending_trigger = None
                self._merge_trigger_locked(InvokeMode.RESPOND, normalized_reason)
                self._barrier_intent = None
                self._kick_locked()
                self._start_memory_idle_locked()
                committed = True
            finally:
                self._lock.release()
        finally:
            if not committed:
                await asyncio.shield(self._release_barrier_intent(intent))
        return grant

    async def request_prepare(self, reason: str, *, high_priority: bool = False) -> bool:
        """Request one admitted PREPARE snapshot, coalescing all intermediate checkpoints."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            admitted = self._request_prepare_locked(
                _required_text(reason, "reason"), high_priority
            )
            self._kick_locked()
            return admitted

    async def request_continue(self, reason: str) -> bool:
        """Request CONTINUE for current facts without creating response permission."""

        async with self._lock:
            if not self.accepting or self._shutdown:
                raise RuntimeError("backend_restarting")
            if self._active_input is None or self._shutdown:
                self._debug("trigger_suppressed", mode="CONTINUE", reason="input_unavailable")
                return False
            self._merge_trigger_locked(InvokeMode.CONTINUE, _required_text(reason, "reason"))
            self._kick_locked()
            return True

    async def mark_external_commit(self) -> None:
        """Record a TaskManager-accepted durable fact so the next real input gets a new causal scope."""

        async with self._lock:
            if self._active_input is None:
                raise RuntimeError("external commit requires an active input")
            self._has_external_commit = True

    async def renderer_ack(self, clause_id: str, *, complete: bool, mode: str = "voice") -> bool:
        """Commit only a complete, current Renderer ACK; stale/partial clauses remain unheard."""

        async with self._lock:
            try:
                clause = self.context_store.get_clause(clause_id)
            except KeyError:
                return False
            if clause.state is ClauseState.DELIVERED:
                return bool(complete)
            origin = self._clause_origins.get(clause_id)
            current = self._active_input
            if (
                complete
                and (
                    origin is None
                    or current is None
                    or origin != (current.input_id, current.revision)
                    or self._user_speaking
                    or self._speech_candidate
                    or self._confirmed_speech
                )
            ):
                if clause.state in {ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING}:
                    self.context_store.ack_clause(clause_id, complete=False)
                self._clause_origins.pop(clause_id, None)
                self._refresh_speech_settled_locked()
                return False
            accepted = self.context_store.ack_clause(clause_id, complete=complete, mode=mode)
            self._clause_origins.pop(clause_id, None)
            self._refresh_speech_settled_locked()
            if accepted:
                self._has_external_commit = True
                self._context_changed_locked()
            return accepted

    async def renderer_disconnected(self) -> None:
        """Clear transient desktop speech state when no Renderer owns playback or microphone capture."""

        async with self._lock:
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = False
            self._response_grant = None
            self._pending_trigger = None
            self._control_generation += 1
            self._barrier_intent = None
            self._cancel_pending_proposals_locked("renderer_disconnected")
            if self._active_invoke is not None:
                self.harness.cancel(self._active_invoke.request.invoke_id)
            if self._active_input is not None:
                self._published_segments.update(segment.segment_id for segment in self._active_input.segments)

    async def wait_until_idle(self) -> None:
        """Wait for pending/control work only; detached Observation jobs intentionally do not block."""

        while True:
            async with self._lock:
                if self._pending_trigger is not None and (
                    self._drain_task is None or self._drain_task.done()
                ):
                    self._kick_locked()
                task = self._drain_task
                scheduled = self._drain_scheduled
            if scheduled:
                await asyncio.sleep(0)
                continue
            if task is None:
                return
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                waiter = asyncio.current_task()
                if waiter is None or waiter.cancelling() or not task.cancelled():
                    raise
                async with self._lock:
                    if self._drain_task is task:
                        self._drain_task = None
                    if self._pending_trigger is not None and not self._shutdown:
                        self._kick_locked()

    async def shutdown(self) -> None:
        """Fence new work, cancel both Memory owners, and await all Profile-scoped operations."""

        first_shutdown = False
        async with self._lock:
            task = self._drain_task
            memory_idle_task = self._memory_idle_task
            memory_archive_task = self._memory_archive_task
            if not self._shutdown:
                first_shutdown = True
                self._shutdown = True
                self._pending_trigger = None
                self._cancel_memory_idle_task_locked()
                self._cancel_memory_archive_task_locked()
                self._cancel_pending_proposals_locked("runtime_shutdown")
                if self._active_invoke is not None:
                    self.harness.cancel(self._active_invoke.request.invoke_id)
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        for memory_task in (memory_idle_task, memory_archive_task):
            if memory_task is not None and memory_task is not asyncio.current_task():
                await asyncio.gather(memory_task, return_exceptions=True)
        if self._task_operations is not None:
            await self._task_operations.shutdown()
        await self.harness.cancel_observations()
        if first_shutdown:
            self._increment_metric("main.runtime.shutdown")

    def _segment_id_locked(self, segment_id: str | None) -> str:
        if segment_id is not None:
            return _required_text(segment_id, "segment id")
        if self._active_input is not None and not self._has_external_commit and self._active_input.segments:
            return self._active_input.segments[-1].segment_id
        return self._new_id("segment")

    def _advance_input_locked(self, transcript: str, segment_id: str) -> None:
        previous = self._active_input
        if previous is None or self._has_external_commit:
            segments = (InputSegment(segment_id, transcript),)
            self._active_input = ActiveInput(self._new_id("input"), 1, transcript, segments)
            self._has_external_commit = False
            self._reset_prepare_admission_locked()
            if previous is not None:
                self._retire_proposals_for_input_locked(previous.input_id)
        else:
            segments = list(previous.segments)
            for index, item in enumerate(segments):
                if item.segment_id == segment_id:
                    segments[index] = InputSegment(segment_id, transcript)
                    break
            else:
                segments.append(InputSegment(segment_id, transcript))
            composed = "\n".join(item.text for item in segments)
            self._active_input = ActiveInput(
                previous.input_id, previous.revision + 1, composed, tuple(segments)
            )
        self._response_grant = None
        self._pending_trigger = None
        self.context_store.append_event("user_segment", {
            "segment_id": segment_id,
            "input_id": self._active_input.input_id,
            "text": transcript,
        })
        self._context_changed_locked(refresh_grant=False)
        self._interrupt_stale_clauses_locked()
        if self._active_invoke is not None and self._active_invoke.request.mode is not InvokeMode.PREPARE:
            self.harness.cancel(self._active_invoke.request.invoke_id)

    def _request_prepare_locked(self, reason: str, high_priority: bool) -> bool:
        active_input = self._active_input
        if active_input is None or self._shutdown:
            self._debug("trigger_suppressed", mode="PREPARE", reason="input_unavailable")
            return False
        if self._prepare_count >= self.prepare_admission.max_prepares_per_input:
            self._debug("trigger_suppressed", mode="PREPARE", reason="prepare_budget")
            return False
        now = self.clock()
        units = _stable_units(active_input.latest_transcript)
        if not high_priority:
            interval_ready = (
                self._last_prepare_at is None
                or now - self._last_prepare_at >= self.prepare_admission.min_interval_seconds
            )
            growth = units - self._last_prepare_units
            if self._prepare_wait_started_at is None and growth > 0:
                self._prepare_wait_started_at = now
            waited = (
                self._prepare_wait_started_at is not None
                and now - self._prepare_wait_started_at >= self.prepare_admission.max_wait_seconds
            )
            enough_growth = growth >= self.prepare_admission.min_growth_units
            punctuation_reward = (
                growth >= self.prepare_admission.punctuation_growth_units
                and bool(_PUNCTUATED.search(active_input.latest_transcript))
            )
            if not interval_ready or not (enough_growth or punctuation_reward or waited):
                self._debug("trigger_suppressed", mode="PREPARE", reason="interval_or_growth")
                return False
        self._merge_trigger_locked(InvokeMode.PREPARE, reason)
        return True

    def _merge_trigger_locked(self, mode: InvokeMode, reason: str) -> None:
        active_input = self._active_input
        if active_input is None:
            self._debug("trigger_suppressed", mode=mode.value, reason="input_unavailable")
            return
        candidate = PendingTrigger(
            active_input.input_id,
            mode,
            reason,
        )
        current = self._pending_trigger
        if current is None or current.input_id != candidate.input_id:
            self._pending_trigger = candidate
            self._debug("trigger_admitted", mode=mode.value, reason=reason)
            return
        if _TRIGGER_PRIORITY[candidate.mode] >= _TRIGGER_PRIORITY[current.mode]:
            self._pending_trigger = candidate
            self._debug("trigger_admitted", mode=mode.value, reason=reason)
        else:
            self._debug("trigger_suppressed", mode=mode.value, reason="higher_priority_pending")

    def _prepare_barrier_locked(self) -> asyncio.Event | None:
        active = self._active_invoke
        if active is not None and active.request.mode is InvokeMode.PREPARE:
            self.harness.cancel(active.request.invoke_id)
            return active.settled
        if self._preinvoke_mode is InvokeMode.PREPARE:
            return self._preinvoke_settled
        return None

    def _settle_preinvoke_locked(self, event: asyncio.Event) -> None:
        event.set()
        if self._preinvoke_settled is event:
            self._preinvoke_settled = None
            self._preinvoke_mode = None

    async def _release_barrier_intent(self, intent: object) -> None:
        """Release and kick only the endpoint barrier still owned by the caller's token."""

        async with self._lock:
            if self._barrier_intent is intent:
                self._barrier_intent = None
                self._kick_locked()

    def _create_grant_locked(self) -> ResponseGrant:
        assert self._active_input is not None
        grant = ResponseGrant(
            self._new_id("grant"),
            self._active_input.input_id,
            self._active_input.revision,
            self._latest_context_version,
        )
        self._response_grant = grant
        return grant

    def _refresh_grant_locked(self) -> ResponseGrant | None:
        grant = self._response_grant
        active_input = self._active_input
        if (
            grant is None
            or active_input is None
            or grant.input_id != active_input.input_id
            or grant.revision != active_input.revision
            or self._speech_candidate
            or self._confirmed_speech
        ):
            return None
        if grant.context_version != self._latest_context_version:
            grant = ResponseGrant(
                grant.grant_id,
                grant.input_id,
                grant.revision,
                self._latest_context_version,
            )
            self._response_grant = grant
        return grant

    def _kick_locked(self) -> None:
        if self._shutdown or not self.accepting or self._barrier_intent is not None or self._pending_trigger is None:
            return
        if self._drain_scheduled:
            return
        if self._drain_task is not None and not self._drain_task.done():
            return
        loop = asyncio.get_running_loop()
        self._drain_scheduled = True
        try:
            loop.call_soon(self._start_scheduled_drain, loop)
        except BaseException:
            self._drain_scheduled = False

    def _start_scheduled_drain(self, loop: asyncio.AbstractEventLoop) -> None:
        """Create a deferred drain outside commit stacks and contain task-factory failure."""

        self._drain_scheduled = False
        if (
            loop.is_closed()
            or self._shutdown
            or not self.accepting
            or self._barrier_intent is not None
            or self._pending_trigger is None
        ):
            return
        if self._drain_task is not None and not self._drain_task.done():
            return
        drain = self._drain()
        try:
            self._drain_task = loop.create_task(drain)
        except BaseException:
            drain.close()

    async def _drain(self) -> None:
        current_task = asyncio.current_task()
        try:
            while True:
                async with self._lock:
                    if (
                        self._shutdown or not self.accepting
                        or self._barrier_intent is not None
                        or self._pending_trigger is None
                    ):
                        return
                    trigger = self._pending_trigger
                    self._pending_trigger = None
                    trigger_generation = self._control_generation
                    preinvoke_settled = asyncio.Event()
                    self._preinvoke_mode = trigger.mode
                    self._preinvoke_settled = preinvoke_settled
                    active_input = self._active_input
                    if active_input is None or trigger.input_id != active_input.input_id:
                        self._settle_preinvoke_locked(preinvoke_settled)
                        continue
                    if trigger.mode is InvokeMode.PREPARE:
                        if self._prepare_count >= self.prepare_admission.max_prepares_per_input:
                            self._settle_preinvoke_locked(preinvoke_settled)
                            continue
                        self._prepare_count += 1
                        self._last_prepare_at = self.clock()
                        self._last_prepare_units = _stable_units(active_input.latest_transcript)
                        self._prepare_wait_started_at = None
                        grant = None
                    else:
                        grant = self._refresh_grant_locked()
                    recall_identity = (
                        active_input.input_id,
                        active_input.revision,
                        self._latest_context_version,
                    )
                    folded = self.context_store.fold()
                    recall_query = active_input.latest_transcript
                if self.memory_service is None:
                    memory_facts: Sequence[Mapping[str, Any]] = ()
                else:
                    try:
                        recalled = await asyncio.wait_for(
                            self.memory_service.recall_context_async(
                                profile_id=self.owner_profile_id,
                                query=recall_query,
                                limit=6,
                                max_chars=self.snapshot_projector.budget.memory_chars,
                            ),
                            timeout=self.memory_recall_timeout_seconds,
                        )
                        text = str(recalled.get("text", "")).strip()
                        memory_facts = ({
                            "text": text,
                            "memory_ids": [
                                str(value)
                                for value in recalled.get("memory_ids", ())
                                if str(value)
                            ],
                        },) if text else ()
                    except Exception:
                        memory_facts = ()
                async with self._lock:
                    current = self._active_input
                    if (
                        self._shutdown or not self.accepting
                        or current is None
                        or trigger_generation != self._control_generation
                        or (current.input_id, current.revision) != recall_identity[:2]
                    ):
                        self._settle_preinvoke_locked(preinvoke_settled)
                        continue
                    if self._latest_context_version != recall_identity[2]:
                        if trigger.mode is InvokeMode.PREPARE:
                            self._prepare_count = max(0, self._prepare_count - 1)
                        self._merge_trigger_locked(trigger.mode, trigger.reason)
                        self._settle_preinvoke_locked(preinvoke_settled)
                        continue
                    surface = self.harness.freeze_surface(
                        trigger.mode,
                        input_id=active_input.input_id,
                        revision=active_input.revision,
                        context_version=self._latest_context_version,
                        response_grant=grant,
                        current_response_grant=grant,
                        speech_candidate=self._speech_candidate,
                        confirmed_speech=self._confirmed_speech,
                    )
                    snapshot = self.snapshot_projector.project(
                        folded,
                        soul=self.soul,
                        memory_facts=memory_facts,
                        runtime_state=self._runtime_state(trigger.mode, trigger.reason, grant),
                        allowed_tools=surface.provider_schemas(),
                        protected_segment_ids=tuple(
                            segment.segment_id for segment in active_input.segments
                        ),
                    )
                    request = InvokeRequest(
                        invoke_id=self._new_id("invoke"),
                        input_id=active_input.input_id,
                        revision=active_input.revision,
                        context_version=self._latest_context_version,
                        mode=trigger.mode,
                        reason=trigger.reason,
                        response_grant=surface.response_grant,
                        snapshot=snapshot,
                        tool_surface=surface,
                        debug_scope=self.debug,
                    )
                    active = _ActiveInvoke(request, asyncio.Event())
                    self._active_invoke = active
                    self._settle_preinvoke_locked(preinvoke_settled)
                try:
                    request.settlement = await self.harness.run(
                        self._provider_chunks(request),
                        invoke_id=request.invoke_id,
                        tool_surface=request.tool_surface,
                    )
                    await self._report_settlement(request)
                finally:
                    async with self._lock:
                        if self._active_invoke is active:
                            self._active_invoke = None
                        active.settled.set()
        finally:
            async with self._lock:
                if self._preinvoke_settled is not None:
                    self._settle_preinvoke_locked(self._preinvoke_settled)
                if self._drain_task is current_task:
                    self._drain_task = None
                if self._pending_trigger is not None and not self._shutdown:
                    self._kick_locked()
            if self.state_listener is not None and not self._shutdown:
                await self.state_listener("idle")

    async def _report_settlement(self, request: InvokeRequest) -> None:
        settlement = request.settlement
        assert settlement is not None
        debug = request.debug_scope
        for receipt in settlement.receipts:
            self._increment_metric(f"main.receipt.{receipt.code}")
            if debug is not None and debug.enabled:
                debug.emit("model", "receipt", invokeId=request.invoke_id, inputId=request.input_id,
                    toolName=receipt.tool_name, code=receipt.code, mode=request.mode.value)
        provider_failed = any(receipt.code == "provider_stream_error" for receipt in settlement.receipts)
        if not settlement.cancelled and not provider_failed and not any(
            receipt.tool_name == "say_to_user" for receipt in settlement.receipts
        ):
            self._increment_metric("main.invoke.silent")
            if debug is not None and debug.enabled:
                debug.emit("model", "silent", invokeId=request.invoke_id, inputId=request.input_id,
                    mode=request.mode.value, silent=True)
        if debug is not None and debug.enabled:
            debug.emit("model", "cancel" if settlement.cancelled else "settled",
                invokeId=request.invoke_id, inputId=request.input_id, mode=request.mode.value,
                cancelled=settlement.cancelled, code="provider_stream_error" if provider_failed else "settled")
        current = self._active_input
        if (settlement.cancelled or self._shutdown or not self.accepting
                or current is None or (current.input_id, current.revision) != (request.input_id, request.revision)):
            return
        if provider_failed and self.error_listener is not None:
            await self.error_listener("main_model_error")

    async def _provider_chunks(self, request: InvokeRequest):
        started_at = time.monotonic()
        debug = request.debug_scope
        first = True
        if debug is not None and debug.enabled:
            debug.emit("model", "invoke_start", invokeId=request.invoke_id, inputId=request.input_id,
                mode=request.mode.value, reason=request.reason, running=True)
        self._increment_metric("main.invoke.started")
        try:
            async for chunk in self.provider(request):
                if first:
                    first = False
                    if debug is not None and debug.enabled:
                        debug.emit("model", "first_chunk", invokeId=request.invoke_id, inputId=request.input_id,
                            elapsedMs=(time.monotonic() - started_at) * 1000)
                yield chunk
        finally:
            if debug is not None and debug.enabled:
                debug.emit("model", "invoke_end", invokeId=request.invoke_id, inputId=request.input_id,
                    mode=request.mode.value, running=False, elapsedMs=(time.monotonic() - started_at) * 1000)
            observe = getattr(self.metrics, "observe", None)
            if callable(observe):
                observe("main.invoke.latency", time.monotonic() - started_at)
            self._increment_metric("main.invoke.finished")

    def _increment_metric(self, name: str) -> None:
        increment = getattr(self.metrics, "increment", None)
        if callable(increment):
            increment(name)

    async def _offer_say(self, call: ToolCall) -> None:
        async with self._lock:
            active = self._active_invoke
            if active is None:
                raise RuntimeError("speech offer has no active invoke")
            request = active.request
            text = str(call.arguments["text"])
            clause_id = self._new_id("clause")
            self.context_store.create_clause(clause_id, text)
            self._speech_settled.clear()
            self._clause_origins[clause_id] = (request.input_id, request.revision)
            offer = RendererClause(
                clause_id,
                text,
                request.invoke_id,
                request.input_id,
                request.revision,
                self.owner_profile_id or "",
                self.generation,
                request.tool_surface.avatar_capabilities.revision,
                f"{request.invoke_id}:{call.call_id}",
                call.call_id,
                call.avatar or {},
            )
        try:
            await self.renderer(offer)
        except asyncio.CancelledError:
            await self.renderer_ack(clause_id, complete=False)
            raise
        except BaseException:
            await self.renderer_failed(clause_id)
            raise

    async def _say_closed(self, event: dict[str, Any]) -> None:
        if self.say_closed_listener is not None:
            await self.say_closed_listener({**event, "generation": self.generation,
                "profile_id": self.owner_profile_id or event["profile_id"]})

    async def renderer_tts_queued(self, clause_id: str) -> bool:
        """Advance a delivery-owned clause only after bounded queue admission."""

        async with self._lock:
            clause = self.context_store.get_clause(clause_id)
            if clause.state is not ClauseState.DRAFT:
                return False
            self.context_store.transition_clause(clause_id, ClauseState.TTS_QUEUED)
            return True

    async def renderer_playing(self, clause_id: str) -> bool:
        """Advance a clause only after its first PCM frame enters the active Renderer."""

        async with self._lock:
            clause = self.context_store.get_clause(clause_id)
            if clause.state is not ClauseState.TTS_QUEUED:
                return False
            self.context_store.transition_clause(clause_id, ClauseState.PLAYING)
            return True

    async def renderer_failed(self, clause_id: str) -> bool:
        """Mark a queue, TTS, or Renderer failure without publishing assistant language."""

        async with self._lock:
            try:
                clause = self.context_store.get_clause(clause_id)
            except KeyError:
                return False
            if clause.state not in {ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING}:
                return False
            self.context_store.transition_clause(clause_id, ClauseState.FAILED)
            self._clause_origins.pop(clause_id, None)
            self._refresh_speech_settled_locked()
            return True

    async def _save_think_note(self, call: ToolCall) -> None:
        note = json.dumps(
            dict(call.arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        async with self._lock:
            self.context_store.append_event("think_note", {"text": note})
            self._context_changed_locked()

    async def _assign_task(self, call: ToolCall) -> dict[str, Any]:
        async with self._lock:
            if self._shutdown or not self.accepting or self._active_input is None or self._task_operations is None:
                raise ToolExecutorError("task_runtime_unavailable")
            result = self._task_operations.propose(self._active_input.input_id, call)
        await asyncio.sleep(0)
        return result

    def _accept_task_fact_locked(self, fact: Mapping[str, Any], *, accepted: bool = False) -> None:
        if accepted:
            self._has_external_commit = True
        if not self.context_store.upsert_task_fact(fact):
            return
        if fact.get("kind") == "task":
            self._has_external_commit = True
        self._context_changed_locked()
        should_continue = fact.get("kind") == "task" or fact.get("status") == "failed"
        if should_continue and self._active_input is not None and not self._shutdown:
            self._merge_trigger_locked(InvokeMode.CONTINUE, "task_fact_updated")
            self._kick_locked()

    async def _task_error(self, code: str) -> None:
        if self.error_listener is not None:
            await self.error_listener(code)

    def _cancel_pending_proposals_locked(self, reason: str) -> None:
        if self._task_operations is not None:
            self._task_operations.cancel_pending(reason)

    def _freeze_pending_proposals_locked(self) -> None:
        if self._task_operations is not None:
            self._task_operations.freeze()

    def _resume_pending_proposals_locked(self) -> None:
        if self._task_operations is not None:
            self._task_operations.resume()

    def _retire_proposals_for_input_locked(self, input_id: str) -> None:
        if self._task_operations is not None:
            self._task_operations.retire_input(input_id)

    def _observation_accepted(self, observation_id: str, call: ToolCall) -> str:
        """Freeze origin, deduplicate same-input arguments, and append running truth at acceptance."""

        active = self._active_invoke
        if active is None:
            raise RuntimeError("Observation acceptance has no active invoke")
        origin = (active.request.input_id, active.request.revision)
        accepted_id, created = self.observation_ledger.start(
            observation_id,
            input_id=origin[0],
            revision=origin[1],
            kind=call.name,
            arguments=call.arguments,
        )
        if not created:
            return accepted_id
        self._observation_origins[observation_id] = origin
        self.context_store.append_event("observation", {
            "id": observation_id,
            "call_id": call.call_id,
            "kind": call.name,
            "status": "running",
            "request": _json_safe(dict(call.arguments)),
            "origin_input_id": origin[0],
            "origin_revision": origin[1],
        })
        self._context_changed_locked()
        return observation_id

    async def _observation_completed(self, outcome: ObservationOutcome) -> None:
        async with self._lock:
            terminal_error = outcome.error_type
            safe_result = None
            if terminal_error is None:
                try:
                    safe_result = _observation_json(outcome.result)
                except BaseException:
                    terminal_error = "ResultNormalizationError"
            status = "failed" if terminal_error else "completed"
            terminal_payload = terminal_error or safe_result
            before = ObservationLedger.terminal_fact(
                self.observation_ledger, outcome.observation_id,
            )
            try:
                terminal, changed = self.observation_ledger.settle(
                    outcome.observation_id, status, terminal_payload,
                )
            except BaseException:
                terminal = ObservationLedger.terminal_fact(
                    self.observation_ledger, outcome.observation_id,
                )
                if terminal is not None:
                    changed = before is None
                else:
                    terminal, changed = ObservationLedger.settle(
                        self.observation_ledger,
                        outcome.observation_id,
                        "failed",
                        "ResultNormalizationError",
                    )
            if not changed:
                return
            if terminal is None:
                return
            self._observation_origins.pop(outcome.observation_id, None)
            payload: dict[str, Any] = {
                "id": outcome.observation_id,
                "call_id": outcome.call_id,
                "kind": terminal["kind"],
                "status": terminal["status"],
                "origin_input_id": terminal["origin_input_id"],
                "origin_revision": terminal["origin_revision"],
            }
            if terminal["status"] == "failed":
                payload["error_type"] = terminal["error_type"]
            else:
                payload["result"] = terminal["result"]
            self.context_store.append_event("observation", payload)
            self._context_changed_locked()
            if self._active_input is not None and not self._shutdown:
                self._merge_trigger_locked(InvokeMode.CONTINUE, "observation_settled")
                self._kick_locked()

    def _effect_fence(self) -> EffectFence:
        active = self._active_invoke
        current = self._active_input
        if active is None or current is None:
            return EffectFence("", "", 0, 0, cancelled=True)
        request = active.request
        current_grant = self._response_grant
        active_grant = request.response_grant
        if (
            active_grant is not None
            and current_grant is not None
            and active_grant.grant_id == current_grant.grant_id
            and active_grant.input_id == current_grant.input_id
            and active_grant.revision == current_grant.revision
        ):
            fence_grant = active_grant
        else:
            fence_grant = current_grant
        return EffectFence(
            active_invoke_id=request.invoke_id,
            input_id=current.input_id,
            revision=current.revision,
            context_version=request.context_version,
            response_grant=fence_grant,
            speech_candidate=self._speech_candidate,
            confirmed_speech=self._confirmed_speech,
            cancelled=self._shutdown or not self.accepting,
        )

    def _runtime_state(
        self, mode: InvokeMode, reason: str, grant: ResponseGrant | None
    ) -> dict[str, Any]:
        assert self._active_input is not None
        return {
            "input_id": self._active_input.input_id,
            "revision": self._active_input.revision,
            "context_version": self._latest_context_version,
            "mode": mode.value,
            "reason": reason,
            "user_speaking": self._user_speaking,
            "speech_candidate": self._speech_candidate,
            "confirmed_speech": self._confirmed_speech,
            "response_grant_id": grant.grant_id if grant else None,
        }

    def _start_memory_idle_locked(self) -> None:
        """Persist confirmed activity and replace only the Profile's idle timer."""

        if self.memory_service is None or self._shutdown or not self.accepting:
            return
        self.context_store.set_last_user_activity(self.wall_clock())
        self._schedule_memory_idle_locked(self.memory_idle_seconds)

    def _resume_memory_idle_locked(self) -> None:
        """Resume one persisted boundary or raw conversation without moving its deadline."""

        if (
            self.memory_service is None
            or self._shutdown
            or not self.accepting
            or self._user_speaking
            or self._confirmed_speech
            or (
                self.context_store.pending_conversation_archive() is None
                and self.context_store.conversation_batch() is None
            )
        ):
            return
        last_activity = self.context_store.get_last_user_activity()
        if last_activity is None:
            last_activity = self.wall_clock()
            self.context_store.set_last_user_activity(last_activity)
        self._schedule_memory_idle_locked(
            max(0.0, last_activity + self.memory_idle_seconds - self.wall_clock())
        )

    def _schedule_memory_idle_locked(self, delay: float) -> None:
        if not self.accepting or self._shutdown:
            return
        self._cancel_memory_idle_task_locked()
        self._memory_idle_task = asyncio.create_task(
            self._archive_conversation_after(delay),
            name=f"main-memory-idle-{self.owner_profile_id}",
        )

    def _cancel_memory_idle_task_locked(self) -> None:
        task, self._memory_idle_task = self._memory_idle_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    def _cancel_memory_archive_task_locked(self) -> None:
        task, self._memory_archive_task = self._memory_archive_task, None
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()

    async def _archive_conversation_after(self, delay: float) -> None:
        """Wait for idle settlement, seal one boundary, and launch the independent worker."""

        current = asyncio.current_task()
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.wait_until_idle()
            await self._wait_for_speech_settlement()
            async with self._lock:
                if self._shutdown or not self.accepting or self._user_speaking or self._confirmed_speech:
                    return
                pending = self.context_store.pending_conversation_archive()
                if pending is None:
                    pending = self.context_store.freeze_conversation_archive()
                    if pending is not None:
                        self._retire_sealed_input_locked()
                if pending is None:
                    self.context_store.clear_last_user_activity_if_idle()
                    return
                if self._memory_idle_task is current:
                    self._memory_idle_task = None
                self._start_memory_archive_locked(pending)
        finally:
            if self._memory_idle_task is current:
                self._memory_idle_task = None

    def _start_memory_archive_locked(self, pending: PendingConversationArchive) -> None:
        """Start the sole sealed-boundary worker, leaving any existing worker untouched."""

        task = self._memory_archive_task
        if task is not None and not task.done():
            return
        self._memory_archive_task = asyncio.create_task(
            self._archive_pending_conversation(pending),
            name=f"main-memory-archive-{self.owner_profile_id}",
        )

    async def _archive_pending_conversation(
        self, pending: PendingConversationArchive,
    ) -> None:
        """Submit one canonical boundary and commit only its Context cleanup on success."""

        current = asyncio.current_task()
        try:
            turns = self.context_store.conversation_archive_turns(pending)
            remembered = await self.memory_service.remember_conversation(
                profile_id=self.owner_profile_id,
                turns=turns,
                source_id=pending.source_id,
            )
            async with self._lock:
                if self._shutdown:
                    return
                self.context_store.commit_conversation_archive(
                    pending, remembered["summary"],
                )
                self._context_changed_locked()
                if self._memory_archive_task is current:
                    self._memory_archive_task = None
                self._resume_memory_idle_locked()
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                if (
                    not self._shutdown
                    and (self._memory_idle_task is None or self._memory_idle_task.done())
                ):
                    self._schedule_memory_idle_locked(self.memory_idle_seconds)
        finally:
            if self._memory_archive_task is current:
                self._memory_archive_task = None

    async def _wait_for_speech_settlement(self) -> None:
        while True:
            async with self._lock:
                if not self.context_store.has_unsettled_clauses():
                    self._speech_settled.set()
                    return
                self._speech_settled.clear()
            await self._speech_settled.wait()

    def _refresh_speech_settled_locked(self) -> None:
        if not self.context_store.has_unsettled_clauses():
            self._speech_settled.set()

    def _context_changed_locked(self, *, refresh_grant: bool = True) -> None:
        self._latest_context_version += 1
        if refresh_grant:
            self._refresh_grant_locked()

    def _interrupt_stale_clauses_locked(self) -> None:
        current = self._active_input
        if current is None:
            return
        coordinates = (current.input_id, current.revision)
        for clause_id, origin in tuple(self._clause_origins.items()):
            if origin == coordinates:
                continue
            clause = self.context_store.get_clause(clause_id)
            if clause.state in {ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING}:
                self.context_store.ack_clause(clause_id, complete=False)
            self._clause_origins.pop(clause_id, None)
        self._refresh_speech_settled_locked()

    def _reset_prepare_admission_locked(self) -> None:
        self._prepare_count = 0
        self._last_prepare_at = None
        self._last_prepare_units = 0
        self._prepare_wait_started_at = None

    def _retire_sealed_input_locked(self) -> None:
        """Retire the causal scope at seal time without changing canonical Context version."""

        active = self._active_input
        if active is not None:
            self._retire_proposals_for_input_locked(active.input_id)
        self._published_segments.clear()
        self._active_input = None
        self._has_external_commit = False
        self._response_grant = None
        self._pending_trigger = None
        self._control_generation += 1
        self._reset_prepare_admission_locked()

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"


def _stable_units(text: str) -> int:
    """Count CJK code points plus English-like words for provider-independent growth admission."""

    cjk = sum(
        1
        for character in text
        if "\u3400" <= character <= "\u9fff"
        or "\uf900" <= character <= "\ufaff"
        or "\u3040" <= character <= "\u30ff"
        or "\uac00" <= character <= "\ud7af"
    )
    return cjk + len(_ENGLISH_WORD.findall(text))


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be non-empty text")
    return value


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (TypeError, ValueError):
        return str(value)[:2_000]


def _observation_json(value: Any) -> Any:
    """Bound and serialize exact built-in JSON values without invoking hostile protocols."""

    state = {"items": 0}

    def normalize(item: Any, depth: int) -> Any:
        if depth > 16:
            raise ValueError("Observation result nesting is too deep")
        if item is None or type(item) is bool:
            return item
        if type(item) is str:
            if len(item.encode("utf-8")) > 16 * 1024:
                raise ValueError("Observation result scalar is too large")
            return item
        if type(item) is int:
            if item.bit_length() > 256:
                raise ValueError("Observation result integer is too large")
            return item
        if type(item) is float:
            if not (-float("inf") < item < float("inf")):
                raise ValueError("Observation result contains a non-finite number")
            return item
        if type(item) is list:
            state["items"] += len(item)
            if state["items"] > 1_000:
                raise ValueError("Observation result has too many items")
            return [normalize(child, depth + 1) for child in item]
        if type(item) is dict:
            state["items"] += len(item)
            if state["items"] > 1_000:
                raise ValueError("Observation result has too many items")
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("Observation result keys must be text")
                if len(key.encode("utf-8")) > 16 * 1024:
                    raise ValueError("Observation result key is too large")
                result[key] = normalize(child, depth + 1)
            return result
        raise TypeError("Observation result is not a plain JSON value")

    normalized = normalize(value, 0)
    serialized = json.dumps(
        normalized, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")
    if len(serialized) > 64 * 1024:
        raise ValueError("Observation result is too large")
    return normalized


__all__ = [
    "ActiveInput",
    "InputSegment",
    "InvokeRequest",
    "MainAgentRuntime",
    "PendingTrigger",
    "PendingTaskProposal",
    "PrepareAdmissionPolicy",
    "RendererClause",
    "TaskProposalStatus",
]
