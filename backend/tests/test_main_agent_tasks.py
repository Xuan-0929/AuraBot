"""Verify retained Main Agent Task proposal and owner-scoped control behavior.

The public contract is small: ``task_assign`` creates a cancellable three-second proposal, confirmed
speech or Profile shutdown cancels an uncommitted proposal, TaskManager acceptance becomes a durable
fact, and controls fresh-read owner/version truth. Race scaffolding for task factories, private caches,
and projection retries was deleted with the speculative implementation branches.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from backend.main_agent import ContextStore, MainAgentRuntime, SnapshotProjector
from backend.main_agent.tasks import TaskBridge
from backend.main_agent.tool_runtime import ToolCall, ToolExecutorError, ToolSurfacePolicy
from backend.task.models import CreateTaskCommand, TaskPage, TaskRecord, TaskStatus


def delta(call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": call_id, "function": {"name": name, "arguments": arguments},
    }]}}]}


def record(*, owner: str = "owner", status: TaskStatus = TaskStatus.PAUSED,
           version: int = 3, row_id: int = 1) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id=uuid4(), row_id=row_id, title="Task", prompt="private prompt",
        working_directory=Path("/private/workspace"), workspace_id="repo",
        owner_profile_id=owner, idempotency_key="key", status=status,
        timeout_seconds=60, active_runtime_ms=0, question_id=None, question_text=None,
        question_options=(), question_answer=None, result_text=None, error_code=None,
        error_message=None, pause_reason=None, rerun_of_task_id=None, version=version,
        created_at=now, updated_at=now, started_at=None, finished_at=None, deleted_at=None,
    )


class Timers:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleepers: list[tuple[float, asyncio.Future[None]]] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        future = asyncio.get_running_loop().create_future()
        self.sleepers.append((self.now + seconds, future))
        try:
            await future
        finally:
            self.sleepers = [item for item in self.sleepers if item[1] is not future]

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in tuple(self.sleepers):
            if deadline <= self.now and not future.done():
                future.set_result(None)
        for _ in range(5):
            await asyncio.sleep(0)


class Manager:
    def __init__(self) -> None:
        self.records: dict[UUID, TaskRecord] = {}
        self.created: list[CreateTaskCommand] = []
        self.controls: list[tuple[str, UUID, int]] = []
        self.events: asyncio.Queue = asyncio.Queue()

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        self.created.append(command)
        task = replace(
            record(owner=command.owner_profile_id, status=TaskStatus.QUEUED),
            title=command.title, prompt=command.prompt, workspace_id=command.workspace_id,
            idempotency_key=command.idempotency_key,
        )
        self.records[task.id] = task
        return task

    async def get_task(self, task_id: UUID, include_deleted: bool = False) -> TaskRecord:
        return self.records[task_id]

    async def list_tasks(self, *, after_row_id=None, limit=50, statuses=None) -> TaskPage:
        items = [item for item in self.records.values() if item.row_id > (after_row_id or 0)]
        if statuses:
            items = [item for item in items if item.status in statuses]
        return TaskPage(tuple(items[:limit]), None)

    async def pause_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        self.controls.append(("pause", task_id, expected_version))
        current = self.records[task_id]
        updated = replace(current, status=TaskStatus.PAUSED, version=current.version + 1)
        self.records[task_id] = updated
        return updated

    async def iter_events(self, after_event_id: int, task_id=None):
        while True:
            yield await self.events.get()


class ProposalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = ContextStore(":memory:")
        self.timers = Timers()
        self.manager = Manager()
        self.requests = []

        async def provider(request):
            self.requests.append(request)
            yield delta(
                "assign-1", "task_assign",
                '{"title":"Research","prompt":"Investigate","workspace_id":"repo"}',
            )

        async def renderer(_clause) -> None:
            return None

        self.runtime = MainAgentRuntime(
            context_store=self.context,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=("repo",), task_controls=(
                    "inspect_tasks", "answer_task_question", "pause_task", "resume_task", "cancel_task",
                ),
            ),
            provider=provider,
            renderer=renderer,
            executors={},
            soul="Soul",
            clock=self.timers.clock,
            proposal_sleep=self.timers.sleep,
            task_manager=self.manager,
            owner_profile_id="profile_a",
        )

    async def asyncTearDown(self) -> None:
        await self.runtime.shutdown()
        self.context.close()

    async def _propose(self) -> None:
        await self.runtime.submit_text("do research")
        await self.runtime.wait_until_idle()
        self.assertEqual(self.runtime.task_proposals[0].status.value, "pending")

    async def test_exact_three_second_proposal_commits_once_and_projects_durable_fact(self) -> None:
        await self._propose()
        await self.timers.advance(2.9)
        self.assertEqual(self.manager.created, [])
        await self.timers.advance(0.1)
        await self.runtime.wait_for_task_proposals()

        self.assertEqual(len(self.manager.created), 1)
        self.assertEqual(self.manager.created[0].owner_profile_id, "profile_a")
        facts = self.context.fold().task_facts
        self.assertTrue(any(fact.get("status") == "queued" for fact in facts))

    async def test_confirmed_barge_in_cancels_pending_proposal(self) -> None:
        await self._propose()
        await self.runtime.vad_start()
        await self.runtime.confirm_speech()
        await self.timers.advance(3.0)
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(self.manager.created, [])
        self.assertEqual(self.runtime.task_proposals[0].status.value, "cancelled")

    async def test_profile_shutdown_cancels_uncommitted_proposal(self) -> None:
        await self._propose()
        await self.runtime.shutdown()
        await self.timers.advance(3.0)
        self.assertEqual(self.manager.created, [])


class TaskBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_inspect_filters_owner_and_control_uses_fresh_version(self) -> None:
        manager = Manager()
        owned = record(owner="profile_a", status=TaskStatus.RUNNING, version=7, row_id=1)
        foreign = record(owner="profile_b", status=TaskStatus.RUNNING, row_id=2)
        manager.records = {owned.id: owned, foreign.id: foreign}
        bridge = TaskBridge(manager, "profile_a")

        inspected = await bridge.inspect_tasks(ToolCall("inspect", "inspect_tasks", {"limit": 20}))
        paused = await bridge.pause_task(ToolCall(
            "pause", "pause_task", {"task_id": str(owned.id)},
        ))

        self.assertEqual([item["task_id"] for item in inspected["tasks"]], [str(owned.id)])
        self.assertNotIn("prompt", inspected["tasks"][0])
        self.assertEqual(manager.controls, [("pause", owned.id, 7)])
        self.assertEqual(paused["version"], 8)
        with self.assertRaises(ToolExecutorError) as raised:
            await bridge.pause_task(ToolCall(
                "foreign", "pause_task", {"task_id": str(foreign.id)},
            ))
        self.assertEqual(raised.exception.code, "task_not_found")
