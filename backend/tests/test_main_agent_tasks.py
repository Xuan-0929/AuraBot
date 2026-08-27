"""Verify the Main Agent Task bridge, proposal grace state machine, and owner-safe controls.

These tests drive the real MainAgentRuntime and TaskBridge against deterministic monotonic timers and
an in-memory TaskManager-shaped fake. They cover proposal freeze/cancel/commit races, cancellation-
immune admission and controls, timer-factory rejection, supervised event replay, bounded bookkeeping,
non-evictable current-input replay, canonical version ordering, owner filtering, and Context projection
without exposing Task process/session internals.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from backend.main_agent import ContextStore, MainAgentRuntime, SnapshotProjector
from backend.main_agent.tasks import TaskBridge, task_fact
from backend.main_agent.tool_runtime import (
    EffectFence, InvokeMode, ResponseGrant, ToolCall, ToolExecutorError, ToolHarness,
    ToolSurfacePolicy,
)
from backend.task.models import (
    CreateTaskCommand, TaskEvent, TaskPage, TaskRecord, TaskStatus, TaskVersionConflict,
)


def delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": index, "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }]}}]}


def record(*, owner: str = "owner", status: TaskStatus = TaskStatus.PAUSED,
           question_id: UUID | None = None, version: int = 3, row_id: int = 1) -> TaskRecord:
    now = datetime.now(UTC)
    return TaskRecord(
        id=uuid4(), row_id=row_id, title="Task", prompt="private prompt",
        working_directory=Path("/private/workspace"), workspace_id="repo",
        owner_profile_id=owner, idempotency_key="key", status=status,
        timeout_seconds=60, active_runtime_ms=0, question_id=question_id,
        question_text="Choose" if question_id else None,
        question_options=("A", "B") if question_id else (), question_answer=None,
        result_text=None, error_code=None, error_message=None, pause_reason=None,
        rerun_of_task_id=None, version=version, created_at=now, updated_at=now,
        started_at=None, finished_at=None, deleted_at=None,
    )


class FakeMonotonicTimers:
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
            self.sleepers = [(deadline, item) for deadline, item in self.sleepers if item is not future]

    async def advance(self, seconds: float) -> None:
        self.now += seconds
        for deadline, future in tuple(self.sleepers):
            if deadline <= self.now and not future.done():
                future.set_result(None)
        for _ in range(5):
            await asyncio.sleep(0)


class FakeTaskManager:
    def __init__(self) -> None:
        self.records: dict[UUID, TaskRecord] = {}
        self.created: list[CreateTaskCommand] = []
        self.controls: list[tuple[str, UUID, int, UUID | None, str | None]] = []
        self.create_started = asyncio.Event()
        self.create_release = asyncio.Event()
        self.create_release.set()
        self.create_error: BaseException | None = None
        self.events: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self.control_started = asyncio.Event()
        self.control_release = asyncio.Event()
        self.control_release.set()

    async def create_task(self, command: CreateTaskCommand) -> TaskRecord:
        self.created.append(command)
        self.create_started.set()
        await self.create_release.wait()
        if self.create_error:
            raise self.create_error
        existing = next((item for item in self.records.values()
                         if item.owner_profile_id == command.owner_profile_id
                         and item.idempotency_key == command.idempotency_key), None)
        if existing:
            return existing
        task = replace(record(owner=command.owner_profile_id, status=TaskStatus.QUEUED),
                       title=command.title, prompt=command.prompt,
                       workspace_id=command.workspace_id,
                       idempotency_key=command.idempotency_key)
        self.records[task.id] = task
        return task

    async def get_task(self, task_id: UUID, include_deleted: bool = False) -> TaskRecord:
        return self.records[task_id]

    async def list_tasks(self, *, after_row_id=None, limit=50, statuses=None) -> TaskPage:
        items = sorted(self.records.values(), key=lambda item: item.row_id)
        items = [item for item in items if item.row_id > (after_row_id or 0)]
        if statuses:
            items = [item for item in items if item.status in statuses]
        visible = items[:limit]
        return TaskPage(tuple(visible), visible[-1].row_id if len(items) > limit else None)

    async def pause_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        self.control_started.set()
        await self.control_release.wait()
        self.controls.append(("pause", task_id, expected_version, None, None))
        current = self.records[task_id]
        updated = replace(current, status=TaskStatus.PAUSED, version=current.version + 1)
        self.records[task_id] = updated
        return updated

    async def resume_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        self.control_started.set()
        await self.control_release.wait()
        self.controls.append(("resume", task_id, expected_version, None, None))
        current = self.records[task_id]
        updated = replace(current, status=TaskStatus.QUEUED, version=current.version + 1)
        self.records[task_id] = updated
        return updated

    async def cancel_task(self, task_id: UUID, expected_version: int) -> TaskRecord:
        self.control_started.set()
        await self.control_release.wait()
        self.controls.append(("cancel", task_id, expected_version, None, None))
        current = self.records[task_id]
        updated = replace(current, status=TaskStatus.CANCELLED, version=current.version + 1)
        self.records[task_id] = updated
        return updated

    async def answer_question(self, task_id: UUID, question_id: UUID, answer: str,
                              expected_version: int) -> TaskRecord:
        self.control_started.set()
        await self.control_release.wait()
        self.controls.append(("answer", task_id, expected_version, question_id, answer))
        current = self.records[task_id]
        updated = replace(current, status=TaskStatus.QUEUED,
                          question_answer=answer, version=current.version + 1)
        self.records[task_id] = updated
        return updated

    async def iter_events(self, after_event_id: int, task_id=None):
        while True:
            yield await self.events.get()


class TaskProposalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = ContextStore(":memory:")
        self.timers = FakeMonotonicTimers()
        self.manager = FakeTaskManager()
        self.requests = []

        async def provider(request):
            self.requests.append(request)
            return []

        self.runtime = MainAgentRuntime(
            context_store=self.context, snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=("repo",),
                task_controls=("inspect_tasks", "answer_task_question", "pause_task",
                               "resume_task", "cancel_task"),
            ),
            provider=provider, renderer=lambda clause: None, executors={}, soul="",
            clock=self.timers.clock, proposal_sleep=self.timers.sleep,
            task_manager=self.manager, owner_profile_id="owner",
        )

    async def asyncTearDown(self) -> None:
        await self.runtime.shutdown()
        self.context.close()

    async def assign(self, call_id: str = "assign"):
        await self.runtime.submit_text("do work")
        await self.runtime.wait_until_idle()
        request = self.requests[-1]
        call = ToolCall(call_id, "task_assign", {
            "title": "Title", "prompt": "Prompt", "workspace_id": "repo",
        })
        return await self.runtime._assign_task(call), request

    async def test_exact_three_second_commit_uses_proposal_id_once(self) -> None:
        receipt, _ = await self.assign()
        self.assertEqual(receipt["status"], "pending_commit")
        await self.timers.advance(2.999)
        self.assertEqual(self.manager.created, [])
        await self.timers.advance(0.001)
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)
        self.assertEqual(self.manager.created[0].idempotency_key, receipt["proposal_id"])
        self.assertEqual(self.manager.created[0].owner_profile_id, "owner")
        self.assertEqual(self.manager.created[0].workspace_id, "repo")
        self.assertFalse(hasattr(self.manager.created[0], "working_directory"))
        self.assertEqual(self.runtime.task_proposals[0].prompt, "")
        self.assertEqual(self.runtime._proposal_tasks, {})

    async def test_candidate_freezes_remaining_and_noise_resumes_it(self) -> None:
        await self.assign()
        await self.timers.advance(1.25)
        await self.runtime.vad_start()
        await self.timers.advance(20)
        self.assertEqual(self.manager.created, [])
        await self.runtime.vad_stop_candidate()
        await self.runtime.reject_speech_candidate()
        await self.timers.advance(1.749)
        self.assertEqual(self.manager.created, [])
        await self.timers.advance(0.001)
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)

    async def test_confirmed_speech_and_explicit_cancel_are_terminal(self) -> None:
        first, _ = await self.assign("speech")
        await self.runtime.vad_start()
        await self.runtime.update_transcript("actually stop", request_prepare=False)
        await self.timers.advance(10)
        self.assertEqual(self.manager.created, [])
        first_fact = next(item for item in self.context.fold().task_facts
                          if item.get("proposal_id") == first["proposal_id"])
        self.assertEqual(first_fact["status"], "cancelled")
        self.assertEqual(self.runtime.task_proposals[0].prompt, "")

        await self.runtime.strong_pause("new request")
        await self.runtime.wait_until_idle()
        second, _ = await self.assign("explicit")
        self.assertTrue(await self.runtime.cancel_task_proposal(second["proposal_id"]))
        await self.timers.advance(10)
        self.assertEqual(self.manager.created, [])

    async def test_nonempty_same_transcript_still_confirms_speech_and_cancels(self) -> None:
        proposal, _ = await self.assign("same-transcript")
        await self.runtime.vad_start()
        self.assertFalse(await self.runtime.update_transcript("do work", request_prepare=False))
        await self.timers.advance(10)
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal["proposal_id"])
        self.assertEqual(fact["status"], "cancelled")

    async def test_deadline_winner_cannot_be_rolled_back_and_duplicate_delivery_reuses_proposal(self) -> None:
        self.manager.create_release.clear()
        first, _ = await self.assign("same")
        duplicate, _ = await self.assign("same")
        self.assertEqual(duplicate["proposal_id"], first["proposal_id"])
        await self.timers.advance(3)
        await self.manager.create_started.wait()
        self.assertFalse(await self.runtime.cancel_task_proposal(first["proposal_id"]))
        await self.runtime.update_transcript("too late", request_prepare=False)
        self.manager.create_release.set()
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("kind") == "task")
        self.assertEqual(fact["status"], "queued")

    async def test_external_timer_cancellation_cannot_strand_a_started_commit(self) -> None:
        self.manager.create_release.clear()
        proposal, _ = await self.assign("external-cancel")
        await self.timers.advance(3)
        await self.manager.create_started.wait()
        timer = self.runtime._proposal_tasks[proposal["proposal_id"]]
        timer.cancel()
        await asyncio.sleep(0)
        self.manager.create_release.set()
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)
        current = next(item for item in self.runtime.task_proposals
                       if item.proposal_id == proposal["proposal_id"])
        self.assertEqual(current.status.value, "committed")

    async def test_external_timer_cancellation_while_pending_terminalizes_failed(self) -> None:
        proposal, _ = await self.assign("external-pending-cancel")
        timer = self.runtime._proposal_tasks[proposal["proposal_id"]]
        timer.cancel()
        self.assertEqual(await asyncio.gather(timer, return_exceptions=True), [None])
        current = next(item for item in self.runtime.task_proposals
                       if item.proposal_id == proposal["proposal_id"])
        self.assertEqual(current.status.value, "failed")
        self.assertNotIn(proposal["proposal_id"], self.runtime._proposal_tasks)
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal["proposal_id"])
        self.assertEqual(fact["status"], "failed")
        self.assertEqual(fact["reason"], "unexpected_timer_cancel")
        self.assertEqual(self.manager.created, [])

    async def test_committing_projection_failure_cannot_prevent_owned_manager_call(self) -> None:
        original_append = self.context.append_event
        failures = 1

        def flaky_append(event_type, payload):
            nonlocal failures
            if event_type == "task_fact" and payload.get("status") == "committing" and failures:
                failures -= 1
                raise RuntimeError("transient committing projection failure")
            return original_append(event_type, payload)

        self.context.append_event = flaky_append
        proposal, _ = await self.assign("committing-projection")
        await self.timers.advance(3)
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)
        current = next(item for item in self.runtime.task_proposals
                       if item.proposal_id == proposal["proposal_id"])
        self.assertEqual(current.status.value, "committed")

    async def test_live_commit_retry_cannot_be_retired_before_projection_finishes(self) -> None:
        proposal, _ = await self.assign("oldest-live-commit")
        proposal_id = proposal["proposal_id"]
        retry = asyncio.Event()
        original_append = self.context.append_event

        def blocked_append(event_type, payload):
            if (
                event_type == "task_fact"
                and payload.get("proposal_id") == proposal_id
                and payload.get("status") == "committed"
                and not retry.is_set()
            ):
                raise RuntimeError("hold terminal projection retry")
            return original_append(event_type, payload)

        async def retry_sleep(_seconds):
            await retry.wait()

        self.context.append_event = blocked_append
        self.runtime._task_event_retry_sleep = retry_sleep
        timer = self.runtime._proposal_tasks[proposal_id]
        await self.timers.advance(3)
        for _ in range(20):
            current = self.runtime._task_proposals.get(proposal_id)
            if current is not None and current.status.value == "committed":
                break
            await asyncio.sleep(0)
        try:
            self.assertIn(proposal_id, self.runtime._proposal_commit_tasks)
            await self.runtime.submit_text("advance while projection retries")
            await self.runtime.wait_until_idle()
            retained_during_retry = proposal_id in self.runtime._task_proposals
        finally:
            retry.set()
        result = await asyncio.gather(timer, return_exceptions=True)
        self.assertTrue(retained_during_retry)
        self.assertEqual(result, [None])
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal_id)
        self.assertEqual(fact["status"], "committed")
        self.assertNotIn(proposal_id, self.runtime._proposal_commit_tasks)
        self.assertNotIn(proposal_id, self.runtime._task_proposals)

    async def test_external_commit_worker_cancellation_during_retry_still_settles(self) -> None:
        proposal, _ = await self.assign("cancel-commit-retry")
        proposal_id = proposal["proposal_id"]
        retry = asyncio.Event()
        original_append = self.context.append_event

        def blocked_append(event_type, payload):
            if (
                event_type == "task_fact"
                and payload.get("proposal_id") == proposal_id
                and payload.get("status") == "committed"
                and not retry.is_set()
            ):
                raise RuntimeError("hold retry until cancellation")
            return original_append(event_type, payload)

        async def retry_sleep(_seconds):
            await retry.wait()

        self.context.append_event = blocked_append
        self.runtime._task_event_retry_sleep = retry_sleep
        timer = self.runtime._proposal_tasks[proposal_id]
        await self.timers.advance(3)
        for _ in range(20):
            worker = self.runtime._proposal_commit_tasks.get(proposal_id)
            current = self.runtime._task_proposals.get(proposal_id)
            if worker is not None and current is not None and current.status.value == "committed":
                break
            await asyncio.sleep(0)
        try:
            worker.cancel()
            await asyncio.sleep(0)
        finally:
            retry.set()
        result = await asyncio.gather(timer, return_exceptions=True)
        self.assertEqual(result, [None])
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal_id)
        self.assertEqual(fact["status"], "committed")

    async def test_commit_worker_cancelled_synchronously_before_start_projects_failed(self) -> None:
        call_id = "cancel-commit-before-start"
        proposal, _ = await self.assign(call_id)
        proposal_id = proposal["proposal_id"]
        original_append = self.context.append_event
        cancelled_workers = []

        def cancel_from_committing_projection(event_type, payload):
            sequence = original_append(event_type, payload)
            if payload.get("proposal_id") == proposal_id and payload.get("status") == "committing":
                worker = self.runtime._proposal_commit_tasks[proposal_id]
                cancelled_workers.append(worker)
                worker.cancel()
            return sequence

        self.context.append_event = cancel_from_committing_projection
        timer = self.runtime._proposal_tasks[proposal_id]
        await self.timers.advance(3)
        result = await asyncio.gather(timer, return_exceptions=True)
        self.assertEqual(result, [None])
        self.assertEqual(len(cancelled_workers), 1)
        self.assertTrue(cancelled_workers[0].cancelled())
        self.assertEqual(self.manager.created, [])
        current = self.runtime._task_proposals[proposal_id]
        self.assertEqual(current.status.value, "failed")
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal_id)
        self.assertEqual(fact["status"], "failed")
        self.assertEqual(fact["reason"], "commit_worker_cancelled")
        self.assertNotIn(proposal_id, self.runtime._proposal_tasks)
        self.assertNotIn(proposal_id, self.runtime._proposal_commit_tasks)

        identity = (current.origin_input_id, call_id)
        self.assertIn(identity, self.runtime._proposal_calls)
        await self.runtime.mark_external_commit()
        await self.runtime.submit_text("advance after failed commit worker")
        await self.runtime.wait_until_idle()
        self.assertNotIn(proposal_id, self.runtime._task_proposals)
        self.assertNotIn(identity, self.runtime._proposal_calls)

    async def test_commit_task_factory_failure_leaves_failed_as_last_projection(self) -> None:
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        for outcome in ("raise", "cancel"):
            with self.subTest(outcome=outcome):
                proposal, _ = await self.assign(f"commit-factory-{outcome}")

                def factory(loop, coroutine, context=None):
                    code = getattr(coroutine, "cr_code", None)
                    if getattr(code, "co_qualname", "").endswith("._owned_proposal_commit"):
                        if outcome == "raise":
                            raise RuntimeError("commit worker rejected")
                        task = asyncio.Task(coroutine, loop=loop, context=context)
                        task.cancel()
                        return task
                    if previous_factory is not None:
                        return previous_factory(loop, coroutine, context=context)
                    return asyncio.Task(coroutine, loop=loop, context=context)

                loop.set_task_factory(factory)
                try:
                    await self.timers.advance(3)
                    await self.runtime.wait_for_task_proposals()
                finally:
                    loop.set_task_factory(previous_factory)
                current = next(item for item in self.runtime.task_proposals
                               if item.proposal_id == proposal["proposal_id"])
                self.assertEqual(current.status.value, "failed")
                fact = next(item for item in self.context.fold().task_facts
                            if item.get("proposal_id") == proposal["proposal_id"])
                self.assertEqual(fact["status"], "failed")
        self.assertEqual(self.manager.created, [])

    async def test_manager_failure_is_terminal_without_retry(self) -> None:
        self.manager.create_error = RuntimeError("private manager failure")
        proposal, _ = await self.assign()
        await self.timers.advance(3)
        await self.runtime.wait_for_task_proposals()
        await self.timers.advance(100)
        self.assertEqual(len(self.manager.created), 1)
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal["proposal_id"])
        self.assertEqual(fact["status"], "failed")
        self.assertNotIn("private", str(fact))

    async def test_manager_cancellation_is_terminal_without_retry(self) -> None:
        self.manager.create_error = asyncio.CancelledError()
        proposal, _ = await self.assign("manager-cancelled")
        await self.timers.advance(3)
        await self.runtime.wait_for_task_proposals()
        self.assertEqual(len(self.manager.created), 1)
        current = next(item for item in self.runtime.task_proposals
                       if item.proposal_id == proposal["proposal_id"])
        self.assertEqual(current.status.value, "failed")

    async def test_shutdown_cancels_pending_but_waits_for_an_accepted_commit(self) -> None:
        pending, _ = await self.assign("pending")
        await self.runtime.shutdown()
        await self.timers.advance(10)
        self.assertEqual(self.manager.created, [])
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == pending["proposal_id"])
        self.assertEqual(fact["status"], "cancelled")

    async def test_shutdown_waits_for_committing_manager_call_and_keeps_accepted_fact(self) -> None:
        self.manager.create_release.clear()
        proposal, _ = await self.assign("shutdown-race")
        await self.timers.advance(3)
        await self.manager.create_started.wait()
        shutdown = asyncio.create_task(self.runtime.shutdown())
        await asyncio.sleep(0)
        self.assertFalse(shutdown.done())
        self.manager.create_release.set()
        await shutdown
        self.assertEqual(len(self.manager.created), 1)
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal["proposal_id"])
        self.assertEqual(fact["status"], "committed")

    async def test_cancelled_shutdown_waiter_cannot_cancel_an_irreversible_commit(self) -> None:
        self.manager.create_release.clear()
        proposal, _ = await self.assign("cancelled-shutdown")
        await self.timers.advance(3)
        await self.manager.create_started.wait()
        shutdown = asyncio.create_task(self.runtime.shutdown())
        await asyncio.sleep(0)
        shutdown.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await shutdown
        self.manager.create_release.set()
        await self.runtime.wait_for_task_proposals()
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("proposal_id") == proposal["proposal_id"])
        self.assertEqual(fact["status"], "committed")

    async def test_owner_events_fold_latest_question_once_into_next_snapshot(self) -> None:
        await self.runtime.startup()
        question = uuid4()
        owned = replace(record(owner="owner", status=TaskStatus.WAITING,
                               question_id=question, version=7), row_id=5)
        foreign = replace(record(owner="other", status=TaskStatus.COMPLETED, version=8), row_id=6)
        self.manager.records.update({owned.id: owned, foreign.id: foreign})
        own_event = TaskEvent(1, 1, owned.id, "task.question_raised", datetime.now(UTC), {})
        foreign_event = TaskEvent(2, 1, foreign.id, "task.status_changed", datetime.now(UTC), {})
        await self.manager.events.put(own_event)
        await self.manager.events.put(foreign_event)
        for _ in range(10):
            if self.context.fold().task_facts:
                break
            await asyncio.sleep(0)
        version_after_first = self.runtime.latest_context_version
        await self.manager.events.put(own_event)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        self.assertEqual(self.runtime.latest_context_version, version_after_first)
        await self.runtime.submit_text("status")
        await self.runtime.wait_until_idle()
        facts = self.requests[-1].snapshot.task_facts
        self.assertEqual(len([item for item in facts if item.get("kind") == "task"]), 1)
        self.assertEqual(facts[0]["question"]["question_id"], str(question))

    async def test_initial_timer_factory_failures_never_publish_stuck_pending(self) -> None:
        await self.runtime.submit_text("factory")
        await self.runtime.wait_until_idle()
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        for outcome in ("raise", "cancel"):
            with self.subTest(outcome=outcome):
                def factory(loop, coroutine, context=None):
                    code = getattr(coroutine, "cr_code", None)
                    if getattr(code, "co_qualname", "").endswith("._run_task_proposal"):
                        if outcome == "raise":
                            raise RuntimeError("timer rejected")
                        task = asyncio.Task(coroutine, loop=loop, context=context)
                        task.cancel()
                        return task
                    if previous_factory is not None:
                        return previous_factory(loop, coroutine, context=context)
                    return asyncio.Task(coroutine, loop=loop, context=context)

                loop.set_task_factory(factory)
                try:
                    with self.assertRaises(ToolExecutorError):
                        await self.runtime._assign_task(ToolCall(
                            f"initial-{outcome}", "task_assign",
                            {"title": "t", "prompt": "p", "workspace_id": "repo"},
                        ))
                finally:
                    loop.set_task_factory(previous_factory)
                self.assertNotIn("pending", {item.status.value for item in self.runtime.task_proposals})
                self.assertEqual(self.runtime._proposal_tasks, {})

    async def test_resume_timer_factory_failures_terminalize_frozen_proposal(self) -> None:
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()
        for outcome in ("raise", "cancel"):
            with self.subTest(outcome=outcome):
                proposal, _ = await self.assign(f"resume-{outcome}")
                await self.runtime.vad_start()

                def factory(loop, coroutine, context=None):
                    code = getattr(coroutine, "cr_code", None)
                    if getattr(code, "co_qualname", "").endswith("._run_task_proposal"):
                        if outcome == "raise":
                            raise RuntimeError("resume rejected")
                        task = asyncio.Task(coroutine, loop=loop, context=context)
                        task.cancel()
                        return task
                    if previous_factory is not None:
                        return previous_factory(loop, coroutine, context=context)
                    return asyncio.Task(coroutine, loop=loop, context=context)

                loop.set_task_factory(factory)
                try:
                    await self.runtime.reject_speech_candidate()
                finally:
                    loop.set_task_factory(previous_factory)
                current = next(item for item in self.runtime.task_proposals
                               if item.proposal_id == proposal["proposal_id"])
                self.assertIn(current.status.value, {"failed", "cancelled"})
                self.assertNotIn(proposal["proposal_id"], self.runtime._proposal_tasks)

    async def test_task_event_listener_retries_stream_and_context_failures_with_replay(self) -> None:
        event = TaskEvent(1, 1, uuid4(), "task.status_changed", datetime.now(UTC), {})
        owned = replace(record(owner="owner", status=TaskStatus.COMPLETED, version=9), id=event.task_id)
        self.manager.records[owned.id] = owned
        calls = 0

        async def replay(after_event_id, task_id=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient stream failure")
            if event.event_id > after_event_id:
                yield event
            await asyncio.Event().wait()

        self.manager.iter_events = replay
        original_append = self.context.append_event
        failures = 1

        def flaky_append(event_type, payload):
            nonlocal failures
            if event_type == "task_fact" and failures:
                failures -= 1
                raise RuntimeError("transient context failure")
            return original_append(event_type, payload)

        self.context.append_event = flaky_append
        self.runtime._task_event_retry_sleep = lambda _seconds: asyncio.sleep(0)
        await self.runtime.startup()
        for _ in range(50):
            if self.context.fold().task_facts:
                break
            await asyncio.sleep(0)
        self.assertGreaterEqual(calls, 3)
        self.assertEqual(self.context.fold().task_facts[0]["task_id"], str(owned.id))
        self.assertEqual(self.runtime._task_fact_versions[str(owned.id)], 9)

    async def test_current_input_proposal_limit_preserves_first_call_replay(self) -> None:
        await self.runtime.submit_text("many")
        await self.runtime.wait_until_idle()
        first = None
        for index in range(256):
            accepted = await self.runtime._assign_task(ToolCall(
                f"bulk-{index}", "task_assign",
                {"title": "t", "prompt": "p", "workspace_id": "repo"},
            ))
            first = first or accepted
            await self.timers.advance(3)
            await self.runtime.wait_for_task_proposals()
        with self.assertRaisesRegex(ToolExecutorError, "^proposal_limit_reached$"):
            await self.runtime._assign_task(ToolCall(
                "bulk-256", "task_assign",
                {"title": "t", "prompt": "p", "workspace_id": "repo"},
            ))
        replay = await self.runtime._assign_task(ToolCall(
            "bulk-0", "task_assign",
            {"title": "ignored", "prompt": "ignored", "workspace_id": "repo"},
        ))
        self.assertEqual(replay["proposal_id"], first["proposal_id"])
        self.assertEqual(len(self.manager.created), 256)
        self.assertEqual(len(self.runtime._task_proposals), 256)
        self.assertEqual(len(self.runtime._proposal_calls), 256)

    async def test_evicted_version_cache_cannot_regress_canonical_task_fact(self) -> None:
        task_id = uuid4()
        newer = replace(record(owner="owner", status=TaskStatus.COMPLETED, version=6), id=task_id)
        older = replace(record(owner="owner", status=TaskStatus.RUNNING, version=5), id=task_id)
        async with self.runtime._lock:
            self.runtime._record_task_fact_locked(task_fact(newer))
            for index in range(512):
                item = replace(record(owner="owner", version=index + 1), id=uuid4())
                self.runtime._record_task_fact_locked(task_fact(item))
            self.assertNotIn(str(task_id), self.runtime._task_fact_versions)
            self.runtime._record_task_fact_locked(task_fact(older))
        self.assertLessEqual(len(self.runtime._task_fact_versions), 512)
        canonical = next(item for item in self.context.fold().task_facts
                         if item.get("task_id") == str(task_id))
        self.assertEqual(canonical["version"], 6)
        self.assertEqual(canonical["status"], "completed")

    async def test_generic_executor_injection_cannot_bypass_runtime_owned_task_assign(self) -> None:
        effects = []

        async def unsafe(call):
            effects.append(call)

        async def provider(request):
            return [delta(
                0, "unsafe", "task_assign",
                '{"title":"t","prompt":"p","workspace_id":"repo"}',
            )]

        context = ContextStore(":memory:")
        runtime = MainAgentRuntime(
            context_store=context, snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(workspace_ids=("repo",)),
            provider=provider, renderer=lambda clause: None,
            executors={"task_assign": unsafe}, soul="",
        )
        try:
            await runtime.submit_text("unsafe")
            await runtime.wait_until_idle()
            self.assertEqual(effects, [])
            self.assertEqual(runtime.invoke_requests[-1].settlement.receipts[0].code,
                             "executor_unavailable")
        finally:
            await runtime.shutdown()
            context.close()

    async def test_started_task_controls_survive_barge_in_and_project_accepted_fact(self) -> None:
        question_id = uuid4()
        cases = (
            ("pause_task", TaskStatus.RUNNING, {"task_id": None}, TaskStatus.PAUSED),
            ("resume_task", TaskStatus.PAUSED, {"task_id": None}, TaskStatus.QUEUED),
            ("cancel_task", TaskStatus.RUNNING, {"task_id": None}, TaskStatus.CANCELLED),
            ("answer_task_question", TaskStatus.WAITING,
             {"task_id": None, "question_id": str(question_id), "answer": "A"}, TaskStatus.QUEUED),
        )
        for index, (name, initial, arguments, expected) in enumerate(cases):
            with self.subTest(name=name):
                task = record(
                    owner="owner", status=initial,
                    question_id=question_id if name == "answer_task_question" else None,
                    version=10,
                )
                self.manager.records[task.id] = task
                arguments = {**arguments, "task_id": str(task.id)}
                self.manager.control_started = asyncio.Event()
                self.manager.control_release = asyncio.Event()

                async def provider(request, *, tool=name, args=arguments, call_index=index):
                    self.requests.append(request)
                    return [delta(call_index, f"control-{call_index}", tool, json.dumps(args))]

                self.runtime.provider = provider
                await self.runtime.submit_text(f"control {name}")
                await self.manager.control_started.wait()
                request = self.requests[-1]
                await self.runtime.update_transcript(f"barge {name}", request_prepare=False)
                self.manager.control_release.set()
                await self.runtime.wait_until_idle()
                receipt = next(item for item in request.settlement.receipts
                               if item.tool_name == name)
                self.assertTrue(receipt.accepted)
                self.assertEqual(receipt.code, "accepted")
                fact = next(item for item in self.context.fold().task_facts
                            if item.get("task_id") == str(task.id))
                self.assertEqual(fact["status"], expected.value)

    async def test_accepted_control_retries_transient_projection_after_barge_in(self) -> None:
        task = record(owner="owner", status=TaskStatus.RUNNING, version=4)
        self.manager.records[task.id] = task
        self.manager.control_release.clear()
        original_append = self.context.append_event
        failures = 1

        def flaky_append(event_type, payload):
            nonlocal failures
            if event_type == "task_fact" and payload.get("task_id") == str(task.id) and failures:
                failures -= 1
                raise RuntimeError("transient accepted projection failure")
            return original_append(event_type, payload)

        self.context.append_event = flaky_append
        self.runtime._task_event_retry_sleep = lambda _seconds: asyncio.sleep(0)

        async def provider(request):
            self.requests.append(request)
            return [delta(0, "project", "pause_task", '{"task_id":"%s"}' % task.id)]

        self.runtime.provider = provider
        await self.runtime.submit_text("pause with transient projection")
        await self.manager.control_started.wait()
        request = self.requests[-1]
        await self.runtime.update_transcript("barge during projection", request_prepare=False)
        self.manager.control_release.set()
        await self.runtime.wait_until_idle()
        self.assertEqual(request.settlement.receipts[0].code, "accepted")
        fact = next(item for item in self.context.fold().task_facts
                    if item.get("task_id") == str(task.id))
        self.assertEqual(fact["status"], "paused")

    async def test_task_control_cancelled_before_owned_worker_start_does_not_act(self) -> None:
        task = record(owner="owner", status=TaskStatus.RUNNING, version=4)
        self.manager.records[task.id] = task

        async def provider(request):
            self.requests.append(request)
            return [delta(0, "prestart", "pause_task", '{"task_id":"%s"}' % task.id)]

        self.runtime.provider = provider
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def factory(loop, coroutine, context=None):
            if previous_factory is not None:
                owned = previous_factory(loop, coroutine, context=context)
            else:
                owned = asyncio.Task(coroutine, loop=loop, context=context)
            code = getattr(coroutine, "cr_code", None)
            if getattr(code, "co_qualname", "").endswith("._owned_task_control"):
                owned.cancel()
            return owned

        loop.set_task_factory(factory)
        try:
            await self.runtime.submit_text("prestart")
            await self.runtime.wait_until_idle()
        finally:
            loop.set_task_factory(previous_factory)
        self.assertEqual(self.manager.controls, [])
        self.assertEqual(self.requests[-1].settlement.receipts[0].code, "invoke_cancelled")


class TaskBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.manager = FakeTaskManager()
        self.own = record(owner="owner", status=TaskStatus.RUNNING, row_id=1)
        self.other = record(owner="other", status=TaskStatus.WAITING,
                            question_id=uuid4(), row_id=2)
        self.manager.records = {self.own.id: self.own, self.other.id: self.other}
        self.bridge = TaskBridge(self.manager, "owner")

    async def test_inspect_filters_owner_and_omits_private_runtime_fields(self) -> None:
        self.manager.records[self.own.id] = replace(self.own, result_text="x" * 20_000)
        result = await self.bridge.inspect_tasks(ToolCall(
            "i", "inspect_tasks", {"limit": 20},
        ))
        self.assertEqual([item["task_id"] for item in result["tasks"]], [str(self.own.id)])
        self.assertNotIn("working_directory", str(result))
        self.assertNotIn("prompt", str(result))
        self.assertNotIn("result", result["tasks"][0])

    async def test_terminal_and_question_facts_remain_bounded_for_snapshot_projection(self) -> None:
        question = replace(
            self.own, status=TaskStatus.WAITING, question_id=uuid4(),
            question_text="q" * 20_000, question_options=tuple("o" * 2_000 for _ in range(20)),
        )
        completed = replace(self.own, status=TaskStatus.COMPLETED, result_text="r" * 20_000)
        question_projection = task_fact(question)
        completed_projection = task_fact(completed)
        self.assertLessEqual(len(question_projection["question"]["text"]), 700)
        self.assertLessEqual(len(question_projection["question"]["options"]), 5)
        self.assertLessEqual(len(completed_projection["result"]), 1_500)

    async def test_controls_fresh_read_latest_version_and_enforce_owner(self) -> None:
        self.manager.records[self.own.id] = replace(self.own, version=9)
        await self.bridge.pause_task(ToolCall("p", "pause_task", {"task_id": str(self.own.id)}))
        self.assertEqual(self.manager.controls[-1][2], 9)
        with self.assertRaisesRegex(ToolExecutorError, "task_not_found"):
            await self.bridge.cancel_task(ToolCall(
                "c", "cancel_task", {"task_id": str(self.other.id)},
            ))

    async def test_answer_rejects_stale_question_before_manager_action(self) -> None:
        question = uuid4()
        waiting = replace(self.own, status=TaskStatus.WAITING,
                          question_id=question, question_text="Current", version=11)
        self.manager.records[self.own.id] = waiting
        with self.assertRaisesRegex(ToolExecutorError, "task_question_stale"):
            await self.bridge.answer_task_question(ToolCall(
                "a", "answer_task_question",
                {"task_id": str(self.own.id), "question_id": str(uuid4()), "answer": "A"},
            ))
        self.assertEqual(self.manager.controls, [])
        await self.bridge.answer_task_question(ToolCall(
            "a", "answer_task_question",
            {"task_id": str(self.own.id), "question_id": str(question), "answer": "A"},
        ))
        self.assertEqual(self.manager.controls[-1][2], 11)

    async def test_task_schemas_are_physical_and_exact(self) -> None:
        policy = ToolSurfacePolicy(
            workspace_ids=("physical-a", "physical-b"),
            task_controls=("inspect_tasks", "pause_task", "cancel_task"),
        )
        grant = ResponseGrant("g", "i", 1, 1)
        prepare = policy.freeze_surface(
            InvokeMode.PREPARE, input_id="i", revision=1, context_version=1,
        )
        respond = policy.freeze_surface(
            InvokeMode.RESPOND, input_id="i", revision=1, context_version=1,
            response_grant=grant, current_response_grant=grant,
        )
        self.assertIn("inspect_tasks", prepare.names)
        self.assertNotIn("pause_task", prepare.names)
        self.assertEqual(policy.definition("task_assign").parameters["properties"]
                         ["workspace_id"]["enum"], ["physical-a", "physical-b"])
        self.assertIn("pause_task", respond.names)
        self.assertIn("cancel_task", respond.names)
        self.assertNotIn("resume_task", respond.names)
        self.assertNotIn("delete_task", {item.name for item in policy.definitions})

    async def test_question_stale_and_version_race_surface_stable_harness_receipts(self) -> None:
        question = uuid4()
        waiting = replace(self.own, status=TaskStatus.WAITING,
                          question_id=question, question_text="Current", version=11)
        self.manager.records[self.own.id] = waiting
        policy = ToolSurfacePolicy(task_controls=("answer_task_question", "pause_task"))
        grant = ResponseGrant("grant", "input", 1, 1)
        fence = EffectFence("invoke", "input", 1, 1, grant)
        harness = ToolHarness(policy, {
            "answer_task_question": self.bridge.answer_task_question,
            "pause_task": self.bridge.pause_task,
        }, state_provider=lambda: fence)
        surface = policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=1, context_version=1,
            response_grant=grant, current_response_grant=grant,
        )
        stale = await harness.run([delta(
            0, "answer", "answer_task_question",
            '{"task_id":"%s","question_id":"%s","answer":"A"}'
            % (self.own.id, uuid4()),
        )], invoke_id="invoke", tool_surface=surface)
        self.assertEqual(stale.receipts[0].code, "task_question_stale")

        async def conflict(task_id, expected_version):
            raise TaskVersionConflict(expected_version, expected_version + 1)

        self.manager.pause_task = conflict
        fence = replace(fence, active_invoke_id="invoke-2")
        raced = await harness.run([delta(
            0, "pause", "pause_task", '{"task_id":"%s"}' % self.own.id,
        )], invoke_id="invoke-2", tool_surface=surface)
        self.assertEqual(raced.receipts[0].code, "task_version_conflict")


if __name__ == "__main__":
    unittest.main()
