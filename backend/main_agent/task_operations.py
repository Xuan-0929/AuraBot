"""Own Main Agent Task proposals, accepted controls and latest-fact synchronization.

Runtime forwards input identities and acoustic freeze/resume/cancel signals. A proposal has one
worker: it waits its three-second grace then calls TaskManager directly. Only pending workers may
be cancelled; accepted controls and committing proposals finish under this owner's lifetime. The
Runtime's existing lock serializes fact publication with input/grant changes. TaskBridge is the
internal owner-scoped adapter; TaskManager alone owns durable Task state. Projection failures are
reported once, while the next startup reconciles current records before following newer events.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any
from uuid import uuid4

from backend.task.models import CreateTaskCommand
from .tasks import TaskBridge, task_fact
from .tool_runtime import ToolCall, ToolExecutorError

logger = logging.getLogger(__name__)


class TaskProposalStatus(StrEnum):
    PENDING = 'pending'
    COMMITTING = 'committing'
    COMMITTED = 'committed'
    CANCELLED = 'cancelled'
    FAILED = 'failed'


@dataclass(frozen=True, slots=True)
class PendingTaskProposal:
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


class TaskOperations:
    def __init__(self, bridge: TaskBridge, lock: asyncio.Lock,
                 publish: Callable[..., None],
                 error: Callable[[str], Awaitable[None]],
                 clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]]) -> None:
        self.bridge, self.lock, self.publish, self.error = bridge, lock, publish, error
        self.clock, self.sleep = clock, sleep
        self._proposals: dict[str, PendingTaskProposal] = {}
        self._calls: dict[tuple[str, str], str] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._controls: set[asyncio.Task[dict[str, Any]]] = set()
        self._events: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def proposals(self) -> tuple[PendingTaskProposal, ...]:
        return tuple(self._proposals.values())

    @property
    def busy(self) -> bool:
        return any(p.status in {TaskProposalStatus.PENDING, TaskProposalStatus.COMMITTING}
                   for p in self._proposals.values()) or bool(self._controls)

    async def startup(self) -> None:
        if self._events is not None or self._closed:
            return
        watermark = await self.bridge.manager.current_event_id()
        async for fact in self.bridge.current_facts():
            async with self.lock:
                self.publish(fact)
        self._events = asyncio.create_task(self._sync_facts(watermark), name='main-agent-task-facts')

    def propose(self, input_id: str, call: ToolCall) -> dict[str, str]:
        """Register one grace window in the Runtime's serialized input section."""
        if self._closed:
            raise ToolExecutorError('task_runtime_unavailable')
        identity = (input_id, call.call_id)
        existing = self._calls.get(identity)
        if existing is not None:
            proposal = self._proposals[existing]
            return {'proposal_id': existing, 'status': 'pending_commit' if proposal.status is TaskProposalStatus.PENDING else proposal.status.value}
        if sum(key[0] == input_id for key in self._calls) >= 256:
            raise ToolExecutorError('proposal_limit_reached')
        identifier = f'proposal-{uuid4().hex}'
        proposal = PendingTaskProposal(identifier, input_id, str(call.arguments['title']),
            str(call.arguments['prompt']), str(call.arguments['workspace_id']),
            self.bridge.owner_profile_id, TaskProposalStatus.PENDING, 3.0, self.clock() + 3.0)
        # A failed initial projection must not leave a task scheduled behind a failed tool call.
        self.publish(self._proposal_fact(proposal))
        self._proposals[identifier] = proposal
        self._calls[identity] = identifier
        self._schedule(proposal)
        return {'proposal_id': identifier, 'status': 'pending_commit'}

    def freeze(self) -> None:
        now = self.clock()
        for proposal in self.proposals:
            if proposal.status is TaskProposalStatus.PENDING and proposal.deadline is not None:
                self._workers.pop(proposal.proposal_id).cancel()
                self._replace(proposal, remaining_seconds=max(0, proposal.deadline - now), deadline=None)

    def resume(self) -> None:
        for proposal in self.proposals:
            if proposal.status is TaskProposalStatus.PENDING and proposal.deadline is None:
                resumed = replace(proposal, deadline=self.clock() + proposal.remaining_seconds)
                self.publish(self._proposal_fact(resumed))
                self._proposals[proposal.proposal_id] = resumed
                self._schedule(resumed)

    def cancel(self, proposal_id: str, reason: str = 'explicit_cancel') -> bool:
        proposal = self._proposals.get(proposal_id)
        if proposal is None or proposal.status is not TaskProposalStatus.PENDING:
            return False
        worker = self._workers.get(proposal_id)
        if worker is not None:
            worker.cancel()
        self._replace(proposal, status=TaskProposalStatus.CANCELLED, prompt='', deadline=None, failure_code=reason)
        return True

    def cancel_pending(self, reason: str) -> None:
        for proposal in self.proposals:
            self.cancel(proposal.proposal_id, reason)

    def retire_input(self, input_id: str) -> None:
        for proposal in self.proposals:
            if proposal.origin_input_id == input_id and proposal.status not in {TaskProposalStatus.PENDING, TaskProposalStatus.COMMITTING}:
                self._proposals.pop(proposal.proposal_id)
                self._calls = {key: value for key, value in self._calls.items() if value != proposal.proposal_id}

    async def execute_control(self, call: ToolCall) -> dict[str, Any]:
        """Own a control until completion; the caller may stop waiting without cancelling it."""
        if self._closed:
            raise ToolExecutorError('task_runtime_unavailable')
        started = False
        def mark_started() -> None:
            nonlocal started
            started = True
        worker = asyncio.create_task(self._control(call, mark_started), name=f'task-control-{call.call_id}')
        self._controls.add(worker)
        worker.add_done_callback(self._control_done)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            if not started:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
            raise

    def _control_done(self, worker: asyncio.Task[dict[str, Any]]) -> None:
        self._controls.discard(worker)
        if not worker.cancelled():
            worker.exception()  # Consume a failure even if the original invoke stopped waiting.

    async def _control(self, call: ToolCall, started: Callable[[], None]) -> dict[str, Any]:
        result = await self.bridge.execute_control(call, action_started=started)
        try:
            async with self.lock:
                self.publish(result, accepted=True)
        except Exception:
            logger.exception('Accepted Task control Context projection failed')
            await self.error('task_context_failed')
            raise
        return result

    async def shutdown(self) -> None:
        async with self.lock:
            self._closed = True
            self.cancel_pending('runtime_shutdown')
            events = self._events
            if events is not None:
                events.cancel()
            workers = (*self._workers.values(), *self._controls)
        if workers:
            await asyncio.gather(*(asyncio.shield(task) for task in workers), return_exceptions=True)
        if events is not None:
            await asyncio.gather(events, return_exceptions=True)

    def _schedule(self, proposal: PendingTaskProposal) -> None:
        self._workers[proposal.proposal_id] = asyncio.create_task(
            self._run(proposal.proposal_id), name=f'task-proposal-{proposal.proposal_id}')

    async def _run(self, identifier: str) -> None:
        try:
            proposal = self._proposals[identifier]
            await self.sleep(max(0, proposal.deadline - self.clock()))
            async with self.lock:
                proposal = self._proposals[identifier]
                if proposal.status is not TaskProposalStatus.PENDING or proposal.deadline is None:
                    return
                proposal = self._replace(proposal, status=TaskProposalStatus.COMMITTING, deadline=None, remaining_seconds=0)
            try:
                accepted = await self.bridge.manager.create_task(CreateTaskCommand(
                    title=proposal.title, prompt=proposal.prompt, workspace_id=proposal.workspace_id,
                    owner_profile_id=proposal.owner_profile_id, idempotency_key=identifier))
            except Exception:
                async with self.lock:
                    self._replace(proposal, status=TaskProposalStatus.FAILED, prompt='', failure_code='task_manager_failed')
                raise
            async with self.lock:
                terminal = replace(proposal, status=TaskProposalStatus.COMMITTED, prompt='', task_id=str(accepted.id))
                self._proposals[identifier] = terminal
                self.publish(task_fact(accepted), accepted=True)
                self.publish(self._proposal_fact(terminal))
        except Exception:
            current = self._proposals[identifier]
            if current.status is TaskProposalStatus.COMMITTING:
                self._proposals[identifier] = replace(current, status=TaskProposalStatus.FAILED,
                    prompt='', failure_code='task_operation_failed')
            logger.exception('Task proposal or Context projection failed')
            await self.error('task_operation_failed')
        finally:
            if self._workers.get(identifier) is asyncio.current_task():
                self._workers.pop(identifier)

    def _replace(self, proposal: PendingTaskProposal, **changes: Any) -> PendingTaskProposal:
        updated = replace(proposal, **changes)
        self._proposals[proposal.proposal_id] = updated
        self.publish(self._proposal_fact(updated))
        return updated

    async def _sync_facts(self, watermark: int) -> None:
        try:
            async for fact in self.bridge.facts(watermark):
                async with self.lock:
                    self.publish(fact)
        except Exception:
            logger.exception('Task Context synchronization failed')
            await self.error('task_context_failed')

    @staticmethod
    def _proposal_fact(proposal: PendingTaskProposal) -> dict[str, Any]:
        fact = {'id': f'proposal:{proposal.proposal_id}', 'kind': 'task_proposal',
                'proposal_id': proposal.proposal_id, 'title': proposal.title,
                'workspace_id': proposal.workspace_id, 'status': proposal.status.value,
                'origin_input_id': proposal.origin_input_id}
        if proposal.status is TaskProposalStatus.PENDING:
            fact.update(grace_state='running' if proposal.deadline is not None else 'frozen',
                        remaining_ms=max(0, round(proposal.remaining_seconds * 1000)))
        if proposal.task_id is not None:
            fact['task_id'] = proposal.task_id
        if proposal.failure_code is not None:
            fact['reason'] = proposal.failure_code
        return fact
