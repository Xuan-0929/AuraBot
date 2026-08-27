"""Own Main Agent input causality, control scheduling, grants, and renderer-confirmed text delivery.

MainAgentRuntime folds normalized transcript/VAD/EOU facts into one ActiveInput, coalesces control
triggers into an event-driven single-flight drain deferred outside commit stacks, freezes Snapshot and
ToolSurface together, and runs provider chunks through ToolHarness with a live effect fence. Safe tool
executors detach through an append-only ObservationLedger with same-input reuse; Renderer text reaches
ContextStore only after complete acknowledgement. Runtime also owns cancellable three-second Task
proposals, cancellation-immune Manager commits/controls, and supervised durable Task-event replay;
current-input proposal identities have a hard admission bound, and canonical Task versions fence stale
retries. TaskBridge fresh-reads Manager state for owner-safe inspection and optimistic-version control.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from backend.task.models import CreateTaskCommand

from .context import ClauseState, ContextStore
from .observations import ObservationLedger
from .snapshot import Snapshot, SnapshotProjector
from .tasks import TaskBridge, task_fact
from .tool_runtime import (
    EffectFence,
    InvokeMode,
    InvokeSettlement,
    ObservationOutcome,
    ObservationLimiter,
    ResponseGrant,
    ToolCall,
    ToolExecutorError,
    ToolHarness,
    ToolSurface,
    ToolSurfacePolicy,
)


ProviderChunks = Iterable[Mapping[str, Any]] | AsyncIterable[Mapping[str, Any]]
ProviderPort = Callable[["InvokeRequest"], ProviderChunks | Awaitable[ProviderChunks]]
RendererPort = Callable[["RendererClause"], Any]
Executor = Callable[[ToolCall], Any]


@dataclass(frozen=True, slots=True)
class ActiveInput:
    """The current lightweight causal scope and its latest stable transcript revision."""

    input_id: str
    revision: int
    latest_transcript: str


@dataclass(frozen=True, slots=True)
class PendingTrigger:
    """One coalesced request for the next control invoke, never an item in a FIFO queue."""

    input_id: str
    mode: InvokeMode
    reason: str
    min_context_version: int


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
    motion_token: str | None = None
    expression_token: str | None = None


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


@dataclass(slots=True)
class _ActiveInvoke:
    request: InvokeRequest
    settled: asyncio.Event


class TaskProposalStatus(StrEnum):
    """Runtime-only lifecycle before and after one TaskManager admission attempt."""

    PENDING = "pending"
    COMMITTING = "committing"
    COMMITTED = "committed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PendingTaskProposal:
    """One immutable view of Runtime-owned proposal timing and terminal admission truth."""

    proposal_id: str
    origin_input_id: str
    title: str
    prompt: str
    workspace_id: str
    owner_profile_id: str
    status: TaskProposalStatus
    remaining_seconds: float
    deadline: float | None
    task_id: str | None = None
    failure_code: str | None = None


_TRIGGER_PRIORITY = {
    InvokeMode.PREPARE: 0,
    InvokeMode.CONTINUE: 1,
    InvokeMode.RESPOND: 2,
}
_PUNCTUATED = re.compile(r"[。！？!?；;.]\s*$")
_ENGLISH_WORD = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*")
_PROPOSAL_LIMIT_PER_INPUT = 256
_TASK_VERSION_LIMIT = 512


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
        memory_facts: Sequence[Mapping[str, Any]] | Callable[[], Sequence[Mapping[str, Any]]] = (),
        clock: Callable[[], float] | None = None,
        prepare_admission: PrepareAdmissionPolicy | None = None,
        observation_ledger: ObservationLedger | None = None,
        observation_limiter: ObservationLimiter | None = None,
        proposal_sleep: Callable[[float], Awaitable[None]] | None = None,
        task_event_retry_sleep: Callable[[float], Awaitable[None]] | None = None,
        task_manager: Any | None = None,
        owner_profile_id: str | None = None,
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
        self.memory_facts = memory_facts
        self.clock = clock or time.monotonic
        self.prepare_admission = prepare_admission or PrepareAdmissionPolicy()
        self.observation_ledger = observation_ledger or ObservationLedger()
        if (task_manager is None) != (owner_profile_id is None):
            raise ValueError("task_manager and owner_profile_id must be configured together")
        self._proposal_sleep = proposal_sleep or asyncio.sleep
        self._task_event_retry_sleep = task_event_retry_sleep or asyncio.sleep
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
        self._shutdown = False
        self._user_speaking = False
        self._speech_candidate = False
        self._confirmed_speech = False
        self._prepare_count = 0
        self._last_prepare_at: float | None = None
        self._last_prepare_units = 0
        self._prepare_wait_started_at: float | None = None
        self._requests: list[InvokeRequest] = []
        self._clause_origins: dict[str, tuple[str, int]] = {}
        self._observation_origins: dict[str, tuple[str, int]] = {}
        self._task_proposals: OrderedDict[str, PendingTaskProposal] = OrderedDict()
        self._proposal_calls: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._proposal_tasks: dict[str, asyncio.Task[None]] = {}
        self._proposal_commit_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_control_tasks: set[asyncio.Task[dict[str, Any]]] = set()
        self._task_fact_versions: OrderedDict[str, int] = OrderedDict()
        self._task_event_task: asyncio.Task[None] | None = None

        runtime_executors = dict(executors)
        for task_tool in (
            "task_assign", "inspect_tasks", "answer_task_question",
            "pause_task", "resume_task", "cancel_task",
        ):
            runtime_executors.pop(task_tool, None)
        runtime_executors["say_to_user"] = self._offer_say
        runtime_executors["note_think_checkpoint"] = self._save_think_note
        if self._task_bridge is not None:
            runtime_executors.update({
                "task_assign": self._assign_task,
                "inspect_tasks": self._task_bridge.inspect_tasks,
                "answer_task_question": self._run_task_control,
                "pause_task": self._run_task_control,
                "resume_task": self._run_task_control,
                "cancel_task": self._run_task_control,
            })
        self.harness = ToolHarness(
            tool_policy,
            runtime_executors,
            state_provider=self._effect_fence,
            observation_acceptance_hook=self._observation_accepted,
            observation_completion_hook=self._observation_completed,
            observation_limiter=observation_limiter,
        )

    @property
    def active_input(self) -> ActiveInput | None:
        return self._active_input

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
    def invoke_requests(self) -> tuple[InvokeRequest, ...]:
        """Return provider requests for diagnostics without exposing mutable scheduler collections."""

        return tuple(self._requests)

    @property
    def task_proposals(self) -> tuple[PendingTaskProposal, ...]:
        """Return immutable proposal views in acceptance order for diagnostics and UI projection."""

        return tuple(self._task_proposals.values())

    async def startup(self) -> None:
        """Start the owner-filtered durable Task event fold when TaskManager integration is configured."""

        async with self._lock:
            self._ensure_task_event_listener_locked()

    async def wait_for_task_proposals(self) -> None:
        """Wait for currently scheduled proposal timers/commits without waiting on the event listener."""

        while True:
            async with self._lock:
                tasks = tuple(
                    task for task in (
                        *self._proposal_tasks.values(), *self._proposal_commit_tasks.values(),
                    ) if not task.done()
                )
            if not tasks:
                return
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    async def cancel_task_proposal(self, proposal_id: str) -> bool:
        """Cancel one still-pending proposal; a committing/durable proposal cannot roll back."""

        async with self._lock:
            proposal = self._task_proposals.get(proposal_id)
            if proposal is None or proposal.status is not TaskProposalStatus.PENDING:
                return False
            self._cancel_proposal_locked(proposal, "explicit_cancel")
            return True

    async def update_transcript(self, text: str, *, request_prepare: bool = True) -> bool:
        """Replace the latest stable ActiveInput transcript, advance revision, and optionally checkpoint."""

        transcript = _required_text(text, "transcript")
        async with self._lock:
            self._cancel_pending_proposals_locked("confirmed_speech")
            self._user_speaking = True
            self._speech_candidate = False
            self._confirmed_speech = True
            if self._active_input is not None and self._active_input.latest_transcript == transcript:
                return False
            self._advance_input_locked(transcript)
            admitted = request_prepare and self._request_prepare_locked("stable_transcript", False)
            self._kick_locked()
            return bool(admitted)

    async def submit_text(self, text: str) -> ResponseGrant:
        """Accept an explicit silent-text submission and grant exactly its current RESPOND coordinates."""

        transcript = _required_text(text, "text submission")
        async with self._lock:
            barrier = self._prepare_barrier_locked()
            self._advance_input_locked(transcript)
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = False
            grant = self._create_grant_locked()
            self._merge_trigger_locked(InvokeMode.RESPOND, "explicit_text_submit")
            self._kick_locked()
        if barrier is not None:
            await barrier.wait()
        return grant

    async def vad_start(self) -> None:
        """Fence response effects immediately for an acoustic speech candidate without changing revision."""

        async with self._lock:
            self._user_speaking = True
            self._speech_candidate = True
            self._confirmed_speech = False
            self._freeze_pending_proposals_locked()

    async def confirm_speech(self) -> None:
        """Confirm barge-in, revoke response authority, and cancel current control/speech ownership."""

        async with self._lock:
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
            cancel_speech = getattr(self.renderer, "cancel_current", None)
        if callable(cancel_speech):
            result = cancel_speech("confirmed_barge_in")
            if inspect.isawaitable(result):
                await result

    async def vad_stop_candidate(self) -> None:
        """Record acoustic silence only; this event never grants or schedules RESPOND."""

        async with self._lock:
            self._user_speaking = False
            self._speech_candidate = True

    async def reject_speech_candidate(self) -> None:
        """Resume a still-valid prior response capability after a VAD candidate is classified as noise."""

        async with self._lock:
            self._user_speaking = False
            self._speech_candidate = False
            self._confirmed_speech = False
            self._resume_pending_proposals_locked()

    async def eou_incomplete(self) -> bool:
        """Settle response-class work, revoke its grant, then admit only high-priority PREPARE."""

        intent = object()
        committed = False
        async with self._lock:
            self._barrier_intent = intent
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
            if self._active_input is None:
                raise RuntimeError("StrongPause requires an active input")
            if self._speech_candidate or self._confirmed_speech:
                self._cancel_pending_proposals_locked("confirmed_speech")
            self._barrier_intent = intent
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
                self._user_speaking = False
                self._speech_candidate = False
                self._confirmed_speech = False
                grant = self._create_grant_locked()
                self._pending_trigger = None
                self._merge_trigger_locked(InvokeMode.RESPOND, normalized_reason)
                self._barrier_intent = None
                self._kick_locked()
                committed = True
                return grant
            finally:
                self._lock.release()
        finally:
            if not committed:
                await asyncio.shield(self._release_barrier_intent(intent))

    async def request_prepare(self, reason: str, *, high_priority: bool = False) -> bool:
        """Request one admitted PREPARE snapshot, coalescing all intermediate checkpoints."""

        async with self._lock:
            admitted = self._request_prepare_locked(
                _required_text(reason, "reason"), high_priority
            )
            self._kick_locked()
            return admitted

    async def request_continue(self, reason: str) -> bool:
        """Request CONTINUE for current facts without creating response permission."""

        async with self._lock:
            if self._active_input is None or self._shutdown:
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

    async def renderer_ack(self, clause_id: str, *, complete: bool) -> bool:
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
                self._notify_renderer_ack(clause_id, complete=False)
                return False
            accepted = self.context_store.ack_clause(clause_id, complete=complete)
            self._clause_origins.pop(clause_id, None)
            self._notify_renderer_ack(clause_id, complete=bool(complete and accepted))
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
        """Fence new work, cancel pending proposals, and await any irreversible commit already started."""

        async with self._lock:
            if self._shutdown:
                task = self._drain_task
            else:
                self._shutdown = True
                self._pending_trigger = None
                self._cancel_pending_proposals_locked("runtime_shutdown")
                if self._active_invoke is not None:
                    self.harness.cancel(self._active_invoke.request.invoke_id)
                task = self._drain_task
            proposal_tasks = tuple(self._proposal_tasks.values())
            commit_tasks = tuple(self._proposal_commit_tasks.values())
            control_tasks = tuple(self._task_control_tasks)
            event_task = self._task_event_task
            if event_task is not None:
                event_task.cancel()
        if task is not None and task is not asyncio.current_task():
            await asyncio.gather(task, return_exceptions=True)
        durable_tasks = (*proposal_tasks, *commit_tasks, *control_tasks)
        if durable_tasks:
            await asyncio.gather(
                *(asyncio.shield(owned) for owned in durable_tasks),
                return_exceptions=True,
            )
        if event_task is not None and event_task is not asyncio.current_task():
            await asyncio.gather(event_task, return_exceptions=True)
        await self.harness.cancel_observations()

    def _advance_input_locked(self, transcript: str) -> None:
        previous = self._active_input
        if previous is None or self._has_external_commit:
            self._active_input = ActiveInput(self._new_id("input"), 1, transcript)
            self._has_external_commit = False
            self._reset_prepare_admission_locked()
            if previous is not None:
                self._retire_proposals_for_input_locked(previous.input_id)
        else:
            self._active_input = ActiveInput(
                previous.input_id, previous.revision + 1, transcript
            )
        self._response_grant = None
        self._pending_trigger = None
        self.context_store.append_event("transcript", {"text": transcript})
        self._context_changed_locked(refresh_grant=False)
        self._interrupt_stale_clauses_locked()
        if self._active_invoke is not None and self._active_invoke.request.mode is not InvokeMode.PREPARE:
            self.harness.cancel(self._active_invoke.request.invoke_id)

    def _request_prepare_locked(self, reason: str, high_priority: bool) -> bool:
        active_input = self._active_input
        if active_input is None or self._shutdown:
            return False
        if self._prepare_count >= self.prepare_admission.max_prepares_per_input:
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
                return False
        self._merge_trigger_locked(InvokeMode.PREPARE, reason)
        return True

    def _merge_trigger_locked(self, mode: InvokeMode, reason: str) -> None:
        active_input = self._active_input
        if active_input is None:
            return
        candidate = PendingTrigger(
            active_input.input_id,
            mode,
            reason,
            self._latest_context_version,
        )
        current = self._pending_trigger
        if current is None or current.input_id != candidate.input_id:
            self._pending_trigger = candidate
            return
        if _TRIGGER_PRIORITY[candidate.mode] >= _TRIGGER_PRIORITY[current.mode]:
            self._pending_trigger = candidate
        elif candidate.min_context_version > current.min_context_version:
            self._pending_trigger = PendingTrigger(
                current.input_id,
                current.mode,
                current.reason,
                candidate.min_context_version,
            )

    def _prepare_barrier_locked(self) -> asyncio.Event | None:
        active = self._active_invoke
        if active is None or active.request.mode is not InvokeMode.PREPARE:
            return None
        self.harness.cancel(active.request.invoke_id)
        return active.settled

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
        if self._shutdown or self._barrier_intent is not None or self._pending_trigger is None:
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
                        self._shutdown
                        or self._barrier_intent is not None
                        or self._pending_trigger is None
                    ):
                        return
                    trigger = self._pending_trigger
                    self._pending_trigger = None
                    active_input = self._active_input
                    if active_input is None or trigger.input_id != active_input.input_id:
                        continue
                    if trigger.mode is InvokeMode.PREPARE:
                        if self._prepare_count >= self.prepare_admission.max_prepares_per_input:
                            continue
                        self._prepare_count += 1
                        self._last_prepare_at = self.clock()
                        self._last_prepare_units = _stable_units(active_input.latest_transcript)
                        self._prepare_wait_started_at = None
                        grant = None
                    else:
                        grant = self._refresh_grant_locked()
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
                        self.context_store.fold(),
                        soul=self.soul,
                        memory_facts=self._memory_facts(),
                        runtime_state=self._runtime_state(trigger.mode, trigger.reason, grant),
                        allowed_tools=surface.provider_schemas(),
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
                    )
                    active = _ActiveInvoke(request, asyncio.Event())
                    self._active_invoke = active
                    self._requests.append(request)
                try:
                    request.settlement = await self.harness.run(
                        self._provider_chunks(request),
                        invoke_id=request.invoke_id,
                        tool_surface=request.tool_surface,
                    )
                finally:
                    async with self._lock:
                        if self._active_invoke is active:
                            self._active_invoke = None
                        active.settled.set()
        finally:
            async with self._lock:
                if self._drain_task is current_task:
                    self._drain_task = None
                if self._pending_trigger is not None and not self._shutdown:
                    self._kick_locked()

    async def _provider_chunks(self, request: InvokeRequest):
        source = self.provider(request)
        if inspect.isawaitable(source):
            source = await source
        if hasattr(source, "__aiter__"):
            async for chunk in source:  # type: ignore[union-attr]
                yield chunk
            return
        for chunk in source:  # type: ignore[union-attr]
            yield chunk

    async def _offer_say(self, call: ToolCall) -> None:
        async with self._lock:
            active = self._active_invoke
            if active is None:
                raise RuntimeError("speech offer has no active invoke")
            request = active.request
            text = str(call.arguments["text"])
            clause_id = self._new_id("clause")
            self.context_store.create_clause(clause_id, text)
            self.context_store.transition_clause(clause_id, ClauseState.TTS_QUEUED)
            self.context_store.transition_clause(clause_id, ClauseState.PLAYING)
            self._clause_origins[clause_id] = (request.input_id, request.revision)
            offer = RendererClause(
                clause_id,
                text,
                request.invoke_id,
                request.input_id,
                request.revision,
                _optional_text(call.arguments.get("motion_token")),
                _optional_text(call.arguments.get("expression_token")),
            )
        try:
            result = self.renderer(offer)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, bool):
                await self.renderer_ack(clause_id, complete=result)
        except asyncio.CancelledError:
            await self.renderer_ack(clause_id, complete=False)
            raise
        except BaseException:
            async with self._lock:
                clause = self.context_store.get_clause(clause_id)
                if clause.state in {ClauseState.DRAFT, ClauseState.TTS_QUEUED, ClauseState.PLAYING}:
                    self.context_store.transition_clause(clause_id, ClauseState.FAILED)
                self._clause_origins.pop(clause_id, None)
            raise

    async def _save_think_note(self, call: ToolCall) -> None:
        note = json.dumps(
            dict(call.arguments), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        async with self._lock:
            self.context_store.append_event("think_note", {"text": note})
            self._context_changed_locked()

    async def _assign_task(self, call: ToolCall) -> dict[str, Any]:
        """Accept one idempotent in-memory proposal and return before its grace timer commits."""

        async with self._lock:
            active_input = self._active_input
            if self._shutdown or active_input is None or self._task_bridge is None:
                raise ToolExecutorError("task_runtime_unavailable")
            self._ensure_task_event_listener_locked()
            identity = (active_input.input_id, call.call_id)
            existing_id = self._proposal_calls.get(identity)
            if existing_id is not None:
                existing = self._task_proposals[existing_id]
                self._proposal_calls.move_to_end(identity)
                self._task_proposals.move_to_end(existing_id)
                return {
                    "proposal_id": existing.proposal_id,
                    "status": "pending_commit" if existing.status is TaskProposalStatus.PENDING
                    else existing.status.value,
                }
            accepted_for_input = sum(
                1 for input_id, _call_id in self._proposal_calls
                if input_id == active_input.input_id
            )
            if accepted_for_input >= _PROPOSAL_LIMIT_PER_INPUT:
                raise ToolExecutorError("proposal_limit_reached")
            proposal_id = self._new_id("proposal")
            proposal = PendingTaskProposal(
                proposal_id=proposal_id,
                origin_input_id=active_input.input_id,
                title=str(call.arguments["title"]),
                prompt=str(call.arguments["prompt"]),
                workspace_id=str(call.arguments["workspace_id"]),
                owner_profile_id=self._task_bridge.owner_profile_id,
                status=TaskProposalStatus.PENDING,
                remaining_seconds=3.0,
                deadline=self.clock() + 3.0,
            )
            self._task_proposals[proposal_id] = proposal
            self._proposal_calls[identity] = proposal_id
            if not self._start_proposal_timer_locked(proposal):
                raise ToolExecutorError("task_proposal_start_failed")
            try:
                self._append_proposal_fact_locked(proposal)
            except BaseException:
                self._proposal_tasks.pop(proposal_id).cancel()
                self._task_proposals[proposal_id] = replace(
                    proposal, status=TaskProposalStatus.FAILED, prompt="", deadline=None,
                    failure_code="context_projection_failed",
                )
                self._retire_terminal_proposals_locked()
                raise ToolExecutorError("task_proposal_start_failed") from None
        await asyncio.sleep(0)
        return {"proposal_id": proposal_id, "status": "pending_commit"}

    async def _run_task_control(self, call: ToolCall) -> dict[str, Any]:
        """Transfer a fenced control to Runtime ownership exactly when its Manager call starts."""

        started = asyncio.Event()
        coroutine = self._owned_task_control(call, started)
        try:
            task = asyncio.create_task(coroutine, name=f"task-control-{call.call_id}")
        except BaseException:
            coroutine.close()
            raise ToolExecutorError("task_control_start_failed") from None
        self._task_control_tasks.add(task)
        try:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if not started.is_set():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
                return await self._await_owned_task(task)
        finally:
            if task.done():
                self._task_control_tasks.discard(task)

    async def _owned_task_control(
        self, call: ToolCall, started: asyncio.Event,
    ) -> dict[str, Any]:
        """Fresh-read, mark Manager-call ownership, then project accepted durable truth."""

        assert self._task_bridge is not None
        async with self._lock:
            self._ensure_task_event_listener_locked()
        result = await self._task_bridge.execute_control(call, action_started=started.set)
        delay = 0.05
        while True:
            async with self._lock:
                self._has_external_commit = True
                try:
                    self._record_task_fact_locked(result)
                except Exception:
                    self._ensure_task_event_listener_locked()
                else:
                    return result
            await self._task_event_retry_sleep(delay)
            delay = min(1.0, delay * 2)

    async def _run_task_proposal(self, proposal_id: str) -> None:
        """Sleep remaining monotonic grace, win pending→committing, then attempt Manager once."""

        current_task = asyncio.current_task()
        commit_task: asyncio.Task[None] | None = None
        commit_started: asyncio.Event | None = None
        commit_projected = False
        try:
            async with self._lock:
                proposal = self._task_proposals[proposal_id]
                if proposal.status is not TaskProposalStatus.PENDING or proposal.deadline is None:
                    return
                delay = max(0.0, proposal.deadline - self.clock())
            await self._proposal_sleep(delay)
            async with self._lock:
                proposal = self._task_proposals[proposal_id]
                if proposal.status is not TaskProposalStatus.PENDING or proposal.deadline is None:
                    return
                proposal = replace(
                    proposal, status=TaskProposalStatus.COMMITTING,
                    remaining_seconds=0.0, deadline=None,
                )
                self._task_proposals[proposal_id] = proposal
                commit_task, commit_started = self._start_proposal_commit_locked(proposal)
                if commit_task is not None:
                    try:
                        self._append_proposal_fact_locked(proposal)
                    except Exception:
                        # Terminal settlement below retries the canonical projection.
                        pass
            if commit_task is not None:
                assert commit_started is not None
                await self._observe_proposal_commit(
                    proposal_id, commit_task, commit_started,
                )
                commit_projected = True
        except asyncio.CancelledError:
            if commit_task is not None:
                assert commit_started is not None
                await self._observe_proposal_commit(
                    proposal_id, commit_task, commit_started,
                )
                commit_projected = True
            else:
                async with self._lock:
                    proposal = self._task_proposals.get(proposal_id)
                    if (
                        self._proposal_tasks.get(proposal_id) is current_task
                        and proposal is not None
                        and proposal.status is TaskProposalStatus.PENDING
                        and proposal.deadline is not None
                    ):
                        self._terminalize_proposal_start_locked(
                            proposal, "unexpected_timer_cancel",
                        )
        finally:
            async with self._lock:
                if self._proposal_tasks.get(proposal_id) is current_task:
                    self._proposal_tasks.pop(proposal_id, None)
                if (
                    commit_task is not None
                    and commit_task.done()
                    and self._proposal_commit_tasks.get(proposal_id) is commit_task
                ):
                    self._proposal_commit_tasks.pop(proposal_id, None)
                    if commit_projected:
                        self._retire_terminal_proposals_locked()

    def _start_proposal_timer_locked(self, proposal: PendingTaskProposal) -> bool:
        """Atomically adopt one live timer or terminalize a task-factory rejection."""

        coroutine = self._run_task_proposal(proposal.proposal_id)
        try:
            task = asyncio.create_task(
                coroutine, name=f"task-proposal-{proposal.proposal_id}",
            )
        except BaseException:
            coroutine.close()
            self._terminalize_proposal_start_locked(proposal, "timer_start_failed")
            return False
        if task.cancelled() or task.cancelling():
            self._terminalize_proposal_start_locked(proposal, "timer_start_cancelled")
            return False
        self._proposal_tasks[proposal.proposal_id] = task
        return True

    def _terminalize_proposal_start_locked(
        self, proposal: PendingTaskProposal, reason: str,
    ) -> None:
        failed = replace(
            proposal, status=TaskProposalStatus.FAILED, prompt="", deadline=None,
            failure_code=reason,
        )
        self._task_proposals[proposal.proposal_id] = failed
        self._append_proposal_fact_locked(failed)
        self._retire_terminal_proposals_locked()

    def _start_proposal_commit_locked(
        self, proposal: PendingTaskProposal,
    ) -> tuple[asyncio.Task[None] | None, asyncio.Event | None]:
        started = asyncio.Event()
        command = CreateTaskCommand(
            title=proposal.title,
            prompt=proposal.prompt,
            workspace_id=proposal.workspace_id,
            owner_profile_id=proposal.owner_profile_id,
            idempotency_key=proposal.proposal_id,
        )
        coroutine = self._owned_proposal_commit(proposal.proposal_id, command, started)
        try:
            task = asyncio.create_task(
                coroutine, name=f"task-proposal-commit-{proposal.proposal_id}",
            )
        except BaseException:
            coroutine.close()
            self._terminalize_proposal_start_locked(proposal, "commit_start_failed")
            return None, None
        if task.cancelled() or task.cancelling():
            self._terminalize_proposal_start_locked(proposal, "commit_start_cancelled")
            return None, None
        self._proposal_commit_tasks[proposal.proposal_id] = task
        return task, started

    async def _observe_proposal_commit(
        self, proposal_id: str, task: asyncio.Task[None], started: asyncio.Event,
    ) -> None:
        """Settle a child cancelled before its first instruction; otherwise observe owned completion."""

        try:
            await self._await_owned_task(task)
        except asyncio.CancelledError:
            if started.is_set() or not task.cancelled():
                raise
            await self._settle_proposal_commit_immune(
                proposal_id, None, "commit_worker_cancelled",
            )

    async def _owned_proposal_commit(
        self, proposal_id: str, command: CreateTaskCommand, started: asyncio.Event,
    ) -> None:
        """Call Manager exactly once in a shielded child, then retry canonical settlement."""

        started.set()
        accepted = None
        failure_code = None
        assert self._task_bridge is not None
        coroutine = self._task_bridge.manager.create_task(command)
        try:
            manager_task = asyncio.create_task(
                coroutine, name=f"task-manager-create-{proposal_id}",
            )
        except BaseException:
            close = getattr(coroutine, "close", None)
            if close is not None:
                close()
            failure_code = "task_manager_failed"
        else:
            try:
                accepted = await self._await_owned_task(manager_task)
            except (Exception, asyncio.CancelledError):
                failure_code = "task_manager_failed"
        await self._settle_proposal_commit_immune(proposal_id, accepted, failure_code)

    async def _settle_proposal_commit_immune(
        self, proposal_id: str, accepted: Any, failure_code: str | None,
    ) -> None:
        """Consume owner cancellation until the one Manager outcome has canonical projection."""

        while True:
            try:
                await self._settle_proposal_commit(proposal_id, accepted, failure_code)
                return
            except asyncio.CancelledError:
                continue

    async def _settle_proposal_commit(
        self, proposal_id: str, accepted: Any, failure_code: str | None,
    ) -> None:
        delay = 0.05
        while True:
            async with self._lock:
                current = self._task_proposals[proposal_id]
                terminal = replace(
                    current,
                    status=(TaskProposalStatus.COMMITTED if accepted is not None
                            else TaskProposalStatus.FAILED),
                    prompt="",
                    task_id=str(accepted.id) if accepted is not None else None,
                    failure_code=failure_code,
                )
                self._task_proposals[proposal_id] = terminal
                try:
                    self._append_proposal_fact_locked(terminal)
                    if accepted is not None:
                        self._record_task_fact_locked(task_fact(accepted))
                    elif self._active_input is not None and not self._shutdown:
                        self._merge_trigger_locked(InvokeMode.CONTINUE, "task_proposal_failed")
                        self._kick_locked()
                except Exception:
                    if accepted is not None:
                        self._has_external_commit = True
                else:
                    return
            await self._task_event_retry_sleep(delay)
            delay = min(1.0, delay * 2)

    @staticmethod
    async def _await_owned_task(task: asyncio.Task[Any]) -> Any:
        """Observe a Runtime-owned task through completion despite cancellation of its waiter."""

        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                if task.done():
                    return task.result()

    def _freeze_pending_proposals_locked(self) -> None:
        """Freeze every live grace timer at its exact monotonic remaining duration."""

        now = self.clock()
        for proposal_id, proposal in tuple(self._task_proposals.items()):
            if proposal.status is not TaskProposalStatus.PENDING or proposal.deadline is None:
                continue
            task = self._proposal_tasks.get(proposal_id)
            if task is not None:
                self._proposal_tasks.pop(proposal_id, None)
                task.cancel()
            frozen = replace(
                proposal,
                remaining_seconds=max(0.0, proposal.deadline - now),
                deadline=None,
            )
            self._task_proposals[proposal_id] = frozen
            self._append_proposal_fact_locked(frozen)

    def _resume_pending_proposals_locked(self) -> None:
        """Resume only timers frozen by a rejected acoustic speech candidate."""

        for proposal_id, proposal in tuple(self._task_proposals.items()):
            if proposal.status is not TaskProposalStatus.PENDING or proposal.deadline is not None:
                continue
            resumed = replace(proposal, deadline=self.clock() + proposal.remaining_seconds)
            self._task_proposals[proposal_id] = resumed
            if not self._start_proposal_timer_locked(resumed):
                continue
            self._append_proposal_fact_locked(resumed)

    def _cancel_pending_proposals_locked(self, reason: str) -> None:
        for proposal in tuple(self._task_proposals.values()):
            if proposal.status is TaskProposalStatus.PENDING:
                self._cancel_proposal_locked(proposal, reason)

    def _cancel_proposal_locked(self, proposal: PendingTaskProposal, reason: str) -> None:
        task = self._proposal_tasks.get(proposal.proposal_id)
        if task is not None:
            task.cancel()
        cancelled = replace(
            proposal, status=TaskProposalStatus.CANCELLED,
            prompt="", deadline=None, failure_code=reason,
        )
        self._task_proposals[proposal.proposal_id] = cancelled
        self._append_proposal_fact_locked(cancelled)
        self._retire_terminal_proposals_locked()

    def _append_proposal_fact_locked(self, proposal: PendingTaskProposal) -> None:
        payload: dict[str, Any] = {
            "id": f"proposal:{proposal.proposal_id}",
            "kind": "task_proposal",
            "proposal_id": proposal.proposal_id,
            "title": proposal.title,
            "workspace_id": proposal.workspace_id,
            "status": proposal.status.value,
            "commit_after_ms": 3_000,
        }
        if proposal.status is TaskProposalStatus.PENDING:
            payload["timer_state"] = "running" if proposal.deadline is not None else "frozen"
            payload["remaining_ms"] = max(0, round(proposal.remaining_seconds * 1_000))
        if proposal.task_id is not None:
            payload["task_id"] = proposal.task_id
        if proposal.failure_code is not None:
            payload["reason"] = proposal.failure_code
        self.context_store.append_event("task_fact", payload)
        self._context_changed_locked()

    def _record_task_fact_locked(self, fact: Mapping[str, Any]) -> None:
        task_id = str(fact["task_id"])
        version = int(fact["version"])
        if self._task_fact_versions.get(task_id, -1) >= version:
            return
        canonical_id = f"task:{task_id}"
        for current in self.context_store.fold().task_facts:
            if current.get("id") == canonical_id and int(current.get("version", -1)) >= version:
                return
        self.context_store.append_event("task_fact", fact)
        self._task_fact_versions[task_id] = version
        self._task_fact_versions.move_to_end(task_id)
        while len(self._task_fact_versions) > _TASK_VERSION_LIMIT:
            self._task_fact_versions.popitem(last=False)
        self._has_external_commit = True
        self._context_changed_locked()
        if self._active_input is not None and not self._shutdown:
            self._merge_trigger_locked(InvokeMode.CONTINUE, "task_fact_updated")
            self._kick_locked()

    def _ensure_task_event_listener_locked(self) -> None:
        if (
            self._task_bridge is None
            or self._shutdown
            or (self._task_event_task is not None and not self._task_event_task.done())
            or not callable(getattr(self._task_bridge.manager, "iter_events", None))
        ):
            return
        self._task_event_task = asyncio.create_task(
            self._task_event_loop(), name="main-agent-task-events",
        )

    async def _task_event_loop(self) -> None:
        assert self._task_bridge is not None
        cursor = 0
        delay = 0.05
        while True:
            try:
                async for event in self._task_bridge.manager.iter_events(cursor):
                    fact = await self._task_bridge.fact_for_event(event)
                    if fact is not None:
                        async with self._lock:
                            if self._shutdown:
                                return
                            self._record_task_fact_locked(fact)
                    cursor = max(cursor, int(event.event_id))
                    delay = 0.05
                async with self._lock:
                    if self._shutdown:
                        return
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await self._task_event_retry_sleep(delay)
            delay = min(1.0, delay * 2)

    def _retire_terminal_proposals_locked(self) -> None:
        active_input_id = self._active_input.input_id if self._active_input is not None else None
        for proposal_id, proposal in tuple(self._task_proposals.items()):
            if (
                proposal.origin_input_id != active_input_id
                and proposal_id not in self._proposal_commit_tasks
                and proposal.status in {
                    TaskProposalStatus.COMMITTED,
                    TaskProposalStatus.CANCELLED,
                    TaskProposalStatus.FAILED,
                }
            ):
                self._remove_proposal_locked(proposal_id)

    def _retire_proposals_for_input_locked(self, input_id: str) -> None:
        for proposal_id, proposal in tuple(self._task_proposals.items()):
            if (
                proposal.origin_input_id == input_id
                and proposal_id not in self._proposal_commit_tasks
                and proposal.status in {
                    TaskProposalStatus.COMMITTED,
                    TaskProposalStatus.CANCELLED,
                    TaskProposalStatus.FAILED,
                }
            ):
                self._remove_proposal_locked(proposal_id)

    def _remove_proposal_locked(self, proposal_id: str) -> None:
        if proposal_id in self._proposal_commit_tasks:
            return
        self._task_proposals.pop(proposal_id, None)
        for identity, current_id in tuple(self._proposal_calls.items()):
            if current_id == proposal_id:
                self._proposal_calls.pop(identity, None)

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
            cancelled=self._shutdown,
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

    def _memory_facts(self) -> Sequence[Mapping[str, Any]]:
        value = self.memory_facts() if callable(self.memory_facts) else self.memory_facts
        return value

    def _context_changed_locked(self, *, refresh_grant: bool = True) -> None:
        self._latest_context_version += 1
        if refresh_grant:
            self._refresh_grant_locked()

    def _notify_renderer_ack(self, clause_id: str, *, complete: bool) -> None:
        """Resolve an optional ACK-aware renderer after the DeliveryLedger has settled the clause."""

        acknowledge = getattr(self.renderer, "acknowledge", None)
        if callable(acknowledge):
            acknowledge(clause_id, complete=complete)

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

    def _reset_prepare_admission_locked(self) -> None:
        self._prepare_count = 0
        self._last_prepare_at = None
        self._last_prepare_units = 0
        self._prepare_wait_started_at = None

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


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


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
    "InvokeRequest",
    "MainAgentRuntime",
    "PendingTrigger",
    "PendingTaskProposal",
    "PrepareAdmissionPolicy",
    "RendererClause",
    "TaskProposalStatus",
]
