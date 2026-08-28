"""Verify Task-5 Observation coordination and the concrete read-only executor trust boundaries.

These tests drive the append-only ledger through MainAgentRuntime/ToolHarness where concurrency and
deduplication matter, while fake HTTP boundary objects exercise Tavily, redirect/DNS validation,
source registration, response limits, and error redaction without using the public network.  Real
temporary workspace descriptors cover canonical paging and filesystem-object denial.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

from backend.main_agent import ContextStore, SnapshotProjector, WorkspaceRegistry
from backend.main_agent.observations import (
    ObservationLedger,
    ObservationSecurityError,
    SafeWebReader,
    SourceRegistry,
    TavilySearchExecutor,
    WorkspaceListExecutor,
    WorkspaceReadExecutor,
    WorkspaceReader,
    _run_deadline_worker,
)
from backend.main_agent.runtime import MainAgentRuntime, RendererClause
from backend.main_agent.tool_runtime import InvokeMode, ObservationOutcome, ToolCall, ToolSurfacePolicy
from backend.main_agent.tool_runtime.harness import ObservationLimiter


def tool_delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {"choices": [{"delta": {"tool_calls": [{
        "index": index,
        "id": call_id,
        "function": {"name": name, "arguments": arguments},
    }]}}]}


class ScriptedProvider:
    def __init__(self, *scripts) -> None:
        self.scripts = deque(scripts)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.scripts.popleft() if self.scripts else []


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.status = status
        self.headers = headers or {"Content-Type": "text/plain; charset=utf-8"}
        self.read_sizes: list[int] = []
        self._offset = 0

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            chunk = self.body[self._offset:]
            self._offset = len(self.body)
            return chunk
        chunk = self.body[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def set_timeout(self, _timeout: float) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class ObservationLedgerTests(unittest.TestCase):
    def test_terminal_histories_are_bounded_without_evicting_running_or_reowning_late_results(self) -> None:
        ledger = ObservationLedger()
        active, _ = ledger.start(
            "active", input_id="active-input", revision=1,
            kind="web_search", arguments={"query": "held"},
        )
        first_terminal = "terminal-0"
        for index in range(300):
            observation_id, created = ledger.start(
                f"terminal-{index}", input_id=f"input-{index}", revision=1,
                kind="web_search", arguments={"query": str(index)},
            )
            self.assertTrue(created)
            self.assertTrue(ledger.complete(observation_id, {"answer": index}))

        self.assertLessEqual(len(ledger._latest), 256)
        self.assertLessEqual(len(ledger._dedupe), 256)
        self.assertLessEqual(len(ledger.events), 512)
        late, owned = ledger.settle(first_terminal, "completed", {"answer": "late"})
        self.assertIsNone(late)
        self.assertFalse(owned)
        self.assertTrue(ledger.complete(active, {"answer": "held completed"}))

    def test_append_only_fold_and_same_input_canonical_argument_dedupe(self) -> None:
        ledger = ObservationLedger()

        first, created = ledger.start(
            "invoke-a:call", input_id="input-1", revision=1,
            kind="web_search", arguments={"b": [2, 1], "a": "x"},
        )
        duplicate, duplicate_created = ledger.start(
            "invoke-b:call", input_id="input-1", revision=2,
            kind="web_search", arguments={"a": "x", "b": [2, 1]},
        )
        other, other_created = ledger.start(
            "invoke-c:call", input_id="input-2", revision=1,
            kind="web_search", arguments={"a": "x", "b": [2, 1]},
        )
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(duplicate, first)
        self.assertTrue(other_created)
        self.assertNotEqual(other, first)
        self.assertEqual(len(ledger.events), 2)

        self.assertTrue(ledger.complete(first, {"answer": "ok"}))
        self.assertFalse(ledger.complete(first, {"answer": "late duplicate"}))
        self.assertTrue(ledger.fail(other, "SafeExecutorError"))
        self.assertEqual([item["status"] for item in ledger.fold()], ["completed", "failed"])
        self.assertEqual(len(ledger.events), 4)

    def test_atomic_settle_returns_canonical_fact_and_transition_ownership(self) -> None:
        class HostileLatePayload:
            def __str__(self):
                raise AssertionError("existing terminal must not inspect late payload")

        ledger = ObservationLedger()
        observation_id, _ = ledger.start(
            "invoke:call", input_id="input", revision=1,
            kind="web_search", arguments={"query": "x"},
        )

        terminal, owned = ledger.settle(observation_id, "completed", {"answer": "first"})
        existing, duplicate_owned = ledger.settle(
            observation_id, "completed", HostileLatePayload(),
        )

        self.assertTrue(owned)
        self.assertFalse(duplicate_owned)
        self.assertEqual(terminal, existing)
        self.assertEqual(existing["status"], "completed")
        self.assertEqual(existing["result"], {"answer": "first"})
        self.assertEqual(len(ledger.events), 2)


class ObservationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = ContextStore(Path(self.directory.name) / "context.sqlite3")
        self.runtimes: list[MainAgentRuntime] = []

    async def asyncTearDown(self) -> None:
        for runtime in self.runtimes:
            await runtime.shutdown()
        self.store.close()
        self.directory.cleanup()

    def runtime(
        self, provider, executors, *, observation_limiter=None, observation_ledger=None,
    ) -> MainAgentRuntime:
        async def renderer(_clause: RendererClause) -> None:
            return None

        runtime = MainAgentRuntime(
            context_store=self.store,
            snapshot_projector=SnapshotProjector(),
            tool_policy=ToolSurfacePolicy(
                workspace_ids=("repo",),
                observation_tools=("web_search", "web_read"),
            ),
            provider=provider,
            renderer=renderer,
            executors=executors,
            soul="test",
            observation_limiter=observation_limiter,
            observation_ledger=observation_ledger,
        )
        self.runtimes.append(runtime)
        return runtime

    async def test_same_active_input_duplicate_reuses_one_job_and_one_terminal_continue(self) -> None:
        release = asyncio.Event()
        calls = 0

        async def search(_call):
            nonlocal calls
            calls += 1
            await release.wait()
            return {"answer": "retained"}

        provider = ScriptedProvider(
            [tool_delta(0, "first", "web_search", '{"query":"same"}')],
            [tool_delta(0, "second", "web_search", '{"query":"same"}')],
            [],
        )
        runtime = self.runtime(provider, {"web_search": search})
        await runtime.update_transcript("version one", request_prepare=False)
        await runtime.request_prepare("first", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.update_transcript("version two", request_prepare=False)
        await runtime.request_prepare("second", high_priority=True)
        await runtime.wait_until_idle()

        self.assertEqual(calls, 1)
        self.assertEqual(provider.requests[1].settlement.receipts[0].code, "observation_reused")
        self.assertEqual(len(runtime.observation_ledger.events), 1)

        release.set()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        self.assertEqual([request.mode for request in provider.requests].count(InvokeMode.CONTINUE), 1)
        self.assertEqual(runtime.observation_ledger.fold()[0]["result"], {"answer": "retained"})

    async def test_at_most_three_global_jobs_are_accepted_and_cancelled_jobs_fail_safely(self) -> None:
        release = asyncio.Event()
        entered = 0
        all_entered = asyncio.Event()

        async def search(_call):
            nonlocal entered
            entered += 1
            if entered == 3:
                all_entered.set()
            await release.wait()

        provider = ScriptedProvider([
            tool_delta(index, f"job-{index}", "web_search", json.dumps({"query": str(index)}))
            for index in range(4)
        ])
        runtime = self.runtime(provider, {"web_search": search})
        await runtime.update_transcript("many", request_prepare=False)
        await runtime.request_prepare("parallel", high_priority=True)
        await runtime.wait_until_idle()
        await asyncio.wait_for(all_entered.wait(), 1)

        self.assertEqual(entered, 3)
        self.assertEqual(
            [receipt.code for receipt in provider.requests[0].settlement.receipts],
            ["detached", "detached", "detached", "observation_limit_reached"],
        )
        self.assertEqual(len(runtime.observation_ledger.fold()), 3)

        await runtime.shutdown()
        outcomes = await runtime.harness.wait_for_observations()
        self.assertEqual(len(outcomes), 3)
        self.assertTrue(all(outcome.error_type == "CancelledError" for outcome in outcomes))
        folded = runtime.observation_ledger.fold()
        self.assertTrue(all(item["status"] == "failed" for item in folded))
        self.assertTrue(all("secret" not in json.dumps(item) for item in folded))

    async def test_three_job_limit_is_shared_across_two_runtime_instances(self) -> None:
        limiter = ObservationLimiter(3)
        release = asyncio.Event()
        entered = 0

        async def search(_call):
            nonlocal entered
            entered += 1
            await release.wait()

        first_provider = ScriptedProvider([
            tool_delta(0, "a", "web_search", '{"query":"a"}'),
            tool_delta(1, "b", "web_search", '{"query":"b"}'),
        ])
        second_provider = ScriptedProvider([
            tool_delta(0, "c", "web_search", '{"query":"c"}'),
            tool_delta(1, "d", "web_search", '{"query":"d"}'),
        ])
        first = self.runtime(first_provider, {"web_search": search}, observation_limiter=limiter)
        second = self.runtime(second_provider, {"web_search": search}, observation_limiter=limiter)
        await first.update_transcript("first", request_prepare=False)
        await second.update_transcript("second", request_prepare=False)
        await asyncio.gather(
            first.request_prepare("read", high_priority=True),
            second.request_prepare("read", high_priority=True),
        )
        await asyncio.gather(first.wait_until_idle(), second.wait_until_idle())

        receipts = [
            receipt.code
            for provider in (first_provider, second_provider)
            for receipt in provider.requests[0].settlement.receipts
        ]
        self.assertEqual(receipts.count("detached"), 3)
        self.assertEqual(receipts.count("observation_limit_reached"), 1)
        self.assertEqual(entered, 3)
        self.assertEqual(limiter.active, 3)
        release.set()
        await asyncio.gather(
            first.harness.wait_for_observations(), second.harness.wait_for_observations(),
        )
        self.assertEqual(limiter.active, 0)

    async def test_observation_task_start_failure_releases_global_admission_and_folds_failure(self) -> None:
        limiter = ObservationLimiter(3)
        provider = ScriptedProvider(
            [tool_delta(0, "start-fail", "web_search", '{"query":"x"}')], [],
        )
        runtime = self.runtime(
            provider, {"web_search": lambda _call: None}, observation_limiter=limiter,
        )
        await runtime.update_transcript("active", request_prepare=False)
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def rejecting_factory(loop, coroutine, context=None):
            code = getattr(coroutine, "cr_code", None)
            if "ToolHarness._detach_observation.<locals>.execute" in getattr(code, "co_qualname", ""):
                raise RuntimeError("task factory rejected Observation")
            if previous_factory is not None:
                return previous_factory(loop, coroutine, context=context)
            return asyncio.Task(coroutine, loop=loop, context=context)

        loop.set_task_factory(rejecting_factory)
        try:
            await runtime.request_prepare("read", high_priority=True)
            await runtime.wait_until_idle()
        finally:
            loop.set_task_factory(previous_factory)
        await runtime.wait_until_idle()

        self.assertEqual(limiter.active, 0)
        self.assertEqual(runtime.observation_ledger.fold()[0]["status"], "failed")
        self.assertEqual(provider.requests[0].settlement.receipts[0].code, "executor_failed")
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_factory_returning_already_cancelled_task_folds_cancelled_terminal_once(self) -> None:
        limiter = ObservationLimiter(1)
        provider = ScriptedProvider(
            [tool_delta(0, "pre-cancel", "web_search", '{"query":"x"}')], [],
        )
        runtime = self.runtime(
            provider, {"web_search": lambda _call: None}, observation_limiter=limiter,
        )
        await runtime.update_transcript("active", request_prepare=False)
        loop = asyncio.get_running_loop()
        previous_factory = loop.get_task_factory()

        def cancelled_factory(loop, coroutine, context=None):
            if previous_factory is not None:
                task = previous_factory(loop, coroutine, context=context)
            else:
                task = asyncio.Task(coroutine, loop=loop, context=context)
            code = getattr(coroutine, "cr_code", None)
            if "ToolHarness._detach_observation.<locals>.execute" in getattr(code, "co_qualname", ""):
                task.cancel()
            return task

        loop.set_task_factory(cancelled_factory)
        try:
            await runtime.request_prepare("read", high_priority=True)
            await runtime.wait_until_idle()
        finally:
            loop.set_task_factory(previous_factory)
        await runtime.wait_until_idle()

        folded = runtime.observation_ledger.fold()
        self.assertEqual(len(folded), 1)
        self.assertEqual(folded[0]["status"], "failed")
        self.assertEqual(folded[0]["error_type"], "CancelledError")
        self.assertEqual(len(runtime.observation_ledger.events), 2)
        self.assertEqual(limiter.active, 0)
        self.assertEqual(provider.requests[0].settlement.receipts[0].code, "executor_failed")
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_cancelled_sync_wrapper_holds_slot_until_real_worker_stops(self) -> None:
        limiter = ObservationLimiter(1)
        sources = SourceRegistry()
        source_id = sources.register("https://example.com/")
        entered = threading.Event()
        release = threading.Event()
        reader = SafeWebReader(sources, resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ])

        def blocking_request(*_args):
            entered.set()
            release.wait(5)
            return FakeResponse(b"done")

        reader._request = blocking_request
        first_provider = ScriptedProvider([
            tool_delta(0, "old", "web_read", json.dumps({"source_id": source_id})),
        ])
        first = self.runtime(first_provider, {"web_read": reader}, observation_limiter=limiter)
        await first.update_transcript("old", request_prepare=False)
        await first.request_prepare("read", high_priority=True)
        await first.wait_until_idle()
        await asyncio.to_thread(entered.wait, 1)
        stopping = asyncio.create_task(first.shutdown())
        await asyncio.sleep(0)

        second_provider = ScriptedProvider([
            tool_delta(0, "new", "web_search", '{"query":"new"}'),
        ])
        second = self.runtime(
            second_provider, {"web_search": lambda _call: {"ok": True}},
            observation_limiter=limiter,
        )
        await second.update_transcript("new", request_prepare=False)
        await second.request_prepare("read", high_priority=True)
        await second.wait_until_idle()
        self.assertEqual(
            second_provider.requests[0].settlement.receipts[0].code,
            "observation_limit_reached",
        )
        self.assertEqual(limiter.active, 1)
        self.assertFalse(stopping.done())

        release.set()
        await asyncio.wait_for(stopping, 1)
        self.assertEqual(limiter.active, 0)

    async def test_queued_worker_timeout_releases_admission_without_starting_job(self) -> None:
        limiter = ObservationLimiter(1)
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = 0

        def blocker():
            blocker_started.set()
            release_blocker.wait(1)

        def queued_job(_deadline):
            nonlocal calls
            calls += 1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        blocker_future = pool.submit(blocker)
        self.assertTrue(blocker_started.wait(1))

        async def search(_call):
            return await _run_deadline_worker(
                queued_job, timeout=0.03, executor=pool,
            )

        provider = ScriptedProvider(
            [tool_delta(0, "queued", "web_search", '{"query":"x"}')], [],
        )
        runtime = self.runtime(
            provider, {"web_search": search}, observation_limiter=limiter,
        )
        try:
            await runtime.update_transcript("active", request_prepare=False)
            await runtime.request_prepare("read", high_priority=True)
            await runtime.wait_until_idle()
            await runtime.harness.wait_for_observations()
            await runtime.wait_until_idle()
            self.assertEqual(limiter.active, 0)
            self.assertFalse(release_blocker.is_set())
            self.assertEqual(calls, 0)
            self.assertEqual(runtime.observation_ledger.fold()[0]["status"], "failed")
        finally:
            release_blocker.set()
            blocker_future.result(timeout=1)
            pool.shutdown(wait=True)
        self.assertEqual(calls, 0)

    async def test_duplicate_reuse_precedes_the_three_job_capacity_check(self) -> None:
        release = asyncio.Event()

        async def search(_call):
            await release.wait()

        provider = ScriptedProvider(
            [
                tool_delta(index, f"job-{index}", "web_search", json.dumps({"query": str(index)}))
                for index in range(3)
            ],
            [tool_delta(0, "duplicate", "web_search", '{"query":"0"}')],
        )
        runtime = self.runtime(provider, {"web_search": search})
        await runtime.update_transcript("first", request_prepare=False)
        await runtime.request_prepare("fill", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.update_transcript("second", request_prepare=False)
        await runtime.request_prepare("reuse", high_priority=True)
        await runtime.wait_until_idle()

        self.assertEqual(provider.requests[1].settlement.receipts[0].code, "observation_reused")
        self.assertEqual(len(runtime.observation_ledger.events), 3)
        release.set()
        await runtime.harness.wait_for_observations()

    async def test_hostile_result_normalization_fails_terminally_and_requests_continue(self) -> None:
        class HostileResult(dict):
            def __str__(self) -> str:
                raise RuntimeError("secret from hostile __str__")

            def __iter__(self):
                raise RuntimeError("secret from hostile iteration")

            def items(self):
                raise RuntimeError("secret from hostile mapping")

        async def search(_call):
            return HostileResult()

        provider = ScriptedProvider(
            [tool_delta(0, "opaque", "web_search", '{"query":"x"}')], [],
        )
        runtime = self.runtime(provider, {"web_search": search})
        await runtime.update_transcript("active", request_prepare=False)
        await runtime.request_prepare("read", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()

        folded = runtime.observation_ledger.fold()
        self.assertEqual(folded[0]["status"], "failed")
        self.assertEqual(folded[0]["error_type"], "ResultNormalizationError")
        self.assertNotIn("secret", json.dumps(folded[0]))
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_huge_integer_becomes_one_normalization_failure(self) -> None:
        provider = ScriptedProvider(
            [tool_delta(0, "bad-result", "web_search", '{"query":"x"}')], [],
        )
        ledger = ObservationLedger()
        runtime = self.runtime(
            provider, {"web_search": lambda _call: 1 << 10_000},
            observation_limiter=ObservationLimiter(1), observation_ledger=ledger,
        )
        await runtime.update_transcript("active", request_prepare=False)
        await runtime.request_prepare("read", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()
        folded = ledger.fold()
        self.assertEqual(folded[0]["status"], "failed")
        self.assertEqual(folded[0]["error_type"], "ResultNormalizationError")
        self.assertEqual(len(ledger.events), 2)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_settle_commit_then_raise_recovers_actual_terminal_exactly_once(self) -> None:
        class CommitThenRaiseLedger(ObservationLedger):
            def settle(self, observation_id, status, payload):
                fact, owned = super().settle(observation_id, status, payload)
                if owned:
                    raise RuntimeError("post-commit secret")
                return fact, owned

        provider = ScriptedProvider(
            [tool_delta(0, "committed", "web_search", '{"query":"x"}')], [],
        )
        ledger = CommitThenRaiseLedger()
        runtime = self.runtime(
            provider, {"web_search": lambda _call: {"answer": "retained"}},
            observation_limiter=ObservationLimiter(1), observation_ledger=ledger,
        )
        await runtime.update_transcript("active", request_prepare=False)
        await runtime.request_prepare("read", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()

        self.assertEqual(ledger.fold()[0]["status"], "completed")
        self.assertEqual(runtime.context_store.fold().observations[0]["status"], "completed")
        self.assertEqual(len(ledger.events), 2)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_settle_precommit_raise_records_sanitized_failure_exactly_once(self) -> None:
        class PrecommitRaiseLedger(ObservationLedger):
            def settle(self, observation_id, status, payload):
                raise RuntimeError("pre-commit secret")

        provider = ScriptedProvider(
            [tool_delta(0, "uncommitted", "web_search", '{"query":"x"}')], [],
        )
        ledger = PrecommitRaiseLedger()
        runtime = self.runtime(
            provider, {"web_search": lambda _call: {"answer": "discarded"}},
            observation_limiter=ObservationLimiter(1), observation_ledger=ledger,
        )
        await runtime.update_transcript("active", request_prepare=False)
        await runtime.request_prepare("read", high_priority=True)
        await runtime.wait_until_idle()
        await runtime.harness.wait_for_observations()
        await runtime.wait_until_idle()

        terminal = ledger.fold()[0]
        context_terminal = runtime.context_store.fold().observations[0]
        self.assertEqual(terminal["status"], "failed")
        self.assertEqual(terminal["error_type"], "ResultNormalizationError")
        self.assertEqual(context_terminal["status"], "failed")
        self.assertEqual(context_terminal["error_type"], "ResultNormalizationError")
        self.assertEqual(len(ledger.events), 2)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(provider.requests[1].mode, InvokeMode.CONTINUE)

    async def test_duplicate_terminal_delivery_is_folded_and_requests_continue_exactly_once(self) -> None:
        provider = ScriptedProvider([], [])
        runtime = self.runtime(provider, {})
        await runtime.update_transcript("active", request_prepare=False)
        observation_id, _ = runtime.observation_ledger.start(
            "invoke:call", input_id=runtime.active_input.input_id,
            revision=runtime.active_input.revision, kind="web_search", arguments={"query": "x"},
        )
        runtime._observation_origins[observation_id] = (
            runtime.active_input.input_id, runtime.active_input.revision,
        )
        outcome = ObservationOutcome("call", "web_search", observation_id, result={"x": 1})

        await runtime._observation_completed(outcome)
        await runtime._observation_completed(outcome)
        await runtime.wait_until_idle()

        self.assertEqual(len(runtime.observation_ledger.events), 2)
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(provider.requests[0].mode, InvokeMode.CONTINUE)


class TavilyAndWebReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_queued_deadline_worker_cancels_before_start_without_releasing_blocker(self) -> None:
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = 0

        def blocker():
            blocker_started.set()
            release_blocker.wait(1)

        def queued_job(_deadline):
            nonlocal calls
            calls += 1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        blocker_future = pool.submit(blocker)
        self.assertTrue(blocker_started.wait(1))
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await _run_deadline_worker(
                    queued_job, timeout=0.03, executor=pool,
                )
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertFalse(release_blocker.is_set())
            self.assertEqual(calls, 0)
        finally:
            release_blocker.set()
            blocker_future.result(timeout=1)
            pool.shutdown(wait=True)
        self.assertEqual(calls, 0)

    async def test_cancelling_queued_worker_prevents_later_execution(self) -> None:
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = 0

        def blocker():
            blocker_started.set()
            release_blocker.wait(1)

        def queued_job(_deadline):
            nonlocal calls
            calls += 1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        blocker_future = pool.submit(blocker)
        self.assertTrue(blocker_started.wait(1))
        task = asyncio.create_task(_run_deadline_worker(
            queued_job, timeout=1.0, executor=pool,
        ))
        await asyncio.sleep(0)
        started = time.monotonic()
        task.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertLess(time.monotonic() - started, 0.25)
            self.assertFalse(release_blocker.is_set())
            self.assertEqual(calls, 0)
        finally:
            release_blocker.set()
            blocker_future.result(timeout=1)
            pool.shutdown(wait=True)
        self.assertEqual(calls, 0)

    async def test_expired_guard_blocks_high_iteration_queue_after_stalled_loop(self) -> None:
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        calls = 0

        def blocker():
            blocker_started.set()
            release_blocker.wait(1)

        def must_not_run(_deadline):
            nonlocal calls
            calls += 1

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        blocker_future = pool.submit(blocker)
        self.assertTrue(blocker_started.wait(1))
        tasks = [asyncio.create_task(_run_deadline_worker(
            must_not_run, timeout=0.02, executor=pool,
        )) for _ in range(100)]
        await asyncio.sleep(0)
        release_timer = threading.Timer(0.03, release_blocker.set)
        release_timer.start()
        time.sleep(0.06)
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.assertTrue(all(type(result) is TimeoutError for result in results))
            self.assertEqual(calls, 0)
        finally:
            release_blocker.set()
            release_timer.join(1)
            blocker_future.result(timeout=1)
            pool.shutdown(wait=True)
        self.assertEqual(calls, 0)

    async def test_delayed_loop_rejects_both_done_result_and_closes_it(self) -> None:
        class Resource:
            def __init__(self):
                self.closed = 0

            def close(self):
                self.closed += 1

        entered = threading.Event()
        release = threading.Event()
        resource = Resource()

        def operation(_deadline):
            entered.set()
            release.wait(1)
            return resource

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            task = asyncio.create_task(_run_deadline_worker(
                operation, timeout=0.02, executor=pool,
            ))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            release_timer = threading.Timer(0.005, release.set)
            release_timer.start()
            time.sleep(0.05)
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                await task
            release_timer.join(1)
        self.assertEqual(resource.closed, 1)

    async def test_caller_cancellation_wins_when_result_and_deadline_are_also_ready(self) -> None:
        class Resource:
            def __init__(self):
                self.closed = 0

            def close(self):
                self.closed += 1

        entered = threading.Event()
        release = threading.Event()
        resource = Resource()

        def operation(_deadline):
            entered.set()
            release.wait(1)
            return resource

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            task = asyncio.create_task(_run_deadline_worker(
                operation, timeout=0.02, executor=pool,
            ))
            self.assertTrue(await asyncio.to_thread(entered.wait, 1))
            release_timer = threading.Timer(0.005, release.set)
            cancel_timer = threading.Timer(0.01, loop.call_soon_threadsafe, args=(task.cancel,))
            release_timer.start()
            cancel_timer.start()
            time.sleep(0.05)
            with self.assertRaises(asyncio.CancelledError):
                await task
            release_timer.join(1)
            cancel_timer.join(1)
        self.assertEqual(resource.closed, 1)

    def test_tavily_late_dns_result_never_starts_connect(self) -> None:
        resolver_started = threading.Event()
        release_resolver = threading.Event()
        resolver_finished = threading.Event()
        connected = []

        def resolver(*_args, **_kwargs):
            resolver_started.set()
            release_resolver.wait(1)
            resolver_finished.set()
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            executor = TavilySearchExecutor(
                "key", SourceRegistry(), submitter=pool.submit, resolver=resolver,
                connection_factory=lambda *_args: connected.append(True),
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "web search provider failed"):
                    executor._search("query", time.monotonic() + 0.01)
            finally:
                release_resolver.set()
            self.assertTrue(resolver_started.is_set())
            self.assertTrue(resolver_finished.wait(1))
            self.assertEqual(connected, [])

    def test_blocking_deadline_future_is_bounded_and_late_result_is_ignored(self) -> None:
        class DeadlineFuture:
            def __init__(self) -> None:
                self.timeouts = []

            def result(self, timeout=None):
                self.timeouts.append(timeout)
                raise concurrent.futures.TimeoutError

        web_future = DeadlineFuture()
        sources = SourceRegistry()
        source_id = sources.register("https://example.com/")
        reader = SafeWebReader(sources, submitter=lambda *_args, **_kwargs: web_future)
        with self.assertRaisesRegex(TimeoutError, "timed out"):
            reader.read(source_id)
        self.assertEqual(len(web_future.timeouts), 1)
        self.assertGreater(web_future.timeouts[0], 0)
        self.assertLessEqual(web_future.timeouts[0], 15.0)

        tavily_future = DeadlineFuture()
        executor = TavilySearchExecutor(
            "key", SourceRegistry(), submitter=lambda *_args, **_kwargs: tavily_future,
        )
        with self.assertRaisesRegex(RuntimeError, "web search provider failed"):
            executor._search("query")
        self.assertEqual(len(tavily_future.timeouts), 1)
        self.assertGreater(tavily_future.timeouts[0], 0)
        self.assertLessEqual(tavily_future.timeouts[0], 10.0)

    async def test_tavily_uses_fixed_contract_registers_sources_and_redacts_provider_error(self) -> None:
        sources = SourceRegistry()
        response = FakeResponse(json.dumps({"results": [{
            "title": "Example", "url": "https://example.com/a", "content": "snippet", "score": 0.9,
        }]}).encode(), headers={"Content-Type": "application/json"})

        class Connection:
            def __init__(self):
                self.requests = []

            def connect(self):
                pass

            def request(self, method, target, body=None, headers=None):
                self.requests.append((method, target, body, headers))

            def getresponse(self):
                return response

            def close(self):
                pass

        connection = Connection()
        executor = TavilySearchExecutor(
            "tavily-super-secret", sources,
            resolver=lambda *_args, **_kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
            ],
            connection_factory=lambda *_args: connection,
        )
        result = await executor(ToolCall("call", "web_search", {"query": "AuraBot"}))

        method, target, body, headers = connection.requests[0]
        payload = json.loads(body)
        self.assertEqual((method, target), ("POST", "/search"))
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(payload, {
            "api_key": "tavily-super-secret", "query": "AuraBot", "search_depth": "basic",
            "max_results": 5, "include_answer": False, "include_raw_content": False,
        })
        self.assertEqual(sources.require(result["results"][0]["source_id"]), "https://example.com/a")

        class FailingConnection(Connection):
            def connect(self):
                raise RuntimeError("upstream leaked tavily-super-secret and query")

        with self.assertRaisesRegex(RuntimeError, "web search provider failed") as raised:
            await TavilySearchExecutor(
                "tavily-super-secret", SourceRegistry(),
                resolver=lambda *_args, **_kwargs: [
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
                ],
                connection_factory=lambda *_args: FailingConnection(),
            )(ToolCall("call", "web_search", {"query": "private query"}))
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("private query", str(raised.exception))

    def test_source_ids_are_required_and_only_http_schemes_can_register(self) -> None:
        sources = SourceRegistry()
        first = sources.register("https://example.com/article")
        self.assertEqual(sources.register("https://example.com/article"), first)
        with self.assertRaises(ObservationSecurityError):
            sources.register("file:///etc/passwd")
        with self.assertRaises(ObservationSecurityError):
            sources.require("source-missing")

    def test_reader_rejects_private_dns_before_transport_and_revalidates_redirects(self) -> None:
        sources = SourceRegistry()
        source_id = sources.register("https://public.example/start")
        requested = []

        def resolver(host, _port, **_kwargs):
            address = "93.184.216.34" if host == "public.example" else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

        reader = SafeWebReader(sources, resolver=resolver)

        def request(url, address, timeout):
            requested.append((url, address, timeout))
            return FakeResponse(b"", status=302, headers={"Location": "http://internal.example/secret"})

        reader._request = request
        with self.assertRaisesRegex(ObservationSecurityError, "public"):
            reader.read(source_id)
        self.assertEqual(len(requested), 1)

        private_source = sources.register("http://private.example/")
        with self.assertRaisesRegex(ObservationSecurityError, "public"):
            reader.read(private_source)
        self.assertEqual(len(requested), 1)

    def test_reader_enforces_redirect_mime_size_timeout_and_normalized_context_limits(self) -> None:
        sources = SourceRegistry()
        source_id = sources.register("https://example.com/start")
        resolver = lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        reader = SafeWebReader(sources, resolver=resolver)

        queue = deque([
            FakeResponse(b"", status=302, headers={"Location": "/one"}),
            FakeResponse(b"", status=302, headers={"Location": "/two"}),
            FakeResponse(b"", status=302, headers={"Location": "/three"}),
            FakeResponse(b"", status=302, headers={"Location": "/four"}),
        ])
        reader._request = lambda *_args: queue.popleft()
        with self.assertRaisesRegex(ObservationSecurityError, "redirect"):
            reader.read(source_id)

        reader._request = lambda *_args: FakeResponse(
            b"binary", headers={"Content-Type": "application/octet-stream"}
        )
        with self.assertRaisesRegex(ObservationSecurityError, "text MIME"):
            reader.read(source_id)

        too_large = FakeResponse(b"x" * (1024 * 1024 + 1))
        reader._request = lambda *_args: too_large
        with self.assertRaisesRegex(ObservationSecurityError, "1 MiB"):
            reader.read(source_id)
        self.assertTrue(too_large.read_sizes)
        self.assertTrue(all(size <= 64 * 1024 for size in too_large.read_sizes))
        self.assertEqual(sum(too_large.read_sizes), 1024 * 1024 + 1)

        encoded = FakeResponse(
            b"compressed", headers={
                "Content-Type": "text/plain", "Content-Encoding": "gzip",
            },
        )
        reader._request = lambda *_args: encoded
        with self.assertRaisesRegex(ObservationSecurityError, "Content-Encoding"):
            reader.read(source_id)
        self.assertEqual(encoded.read_sizes, [])

        class RepeatedHeaders:
            def get(self, name, default=None):
                return default

            def get_all(self, name):
                if name.casefold() == "content-type":
                    return ["text/plain"]
                if name.casefold() == "content-encoding":
                    return ["identity", "identity, gzip"]
                return []

        repeated = FakeResponse(b"encoded", headers=RepeatedHeaders())
        reader._request = lambda *_args: repeated
        with self.assertRaisesRegex(ObservationSecurityError, "Content-Encoding"):
            reader.read(source_id)
        self.assertEqual(repeated.read_sizes, [])

        html = ("<html><script>bad()</script><body>hello&nbsp;world " + "界" * 70_000 + "</body></html>").encode()
        reader._request = lambda *_args: FakeResponse(html, headers={"Content-Type": "text/html; charset=utf-8"})
        result = reader.read(source_id)
        self.assertNotIn("bad()", result["content"])
        self.assertLessEqual(len(result["content"].encode()), 64 * 1024)

        with patch("backend.main_agent.observations.time.monotonic", side_effect=[0.0, 16.0]):
            reader._request = lambda *_args: FakeResponse(b"late")
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                reader.read(source_id)


class WorkspaceReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.directory.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "notes.txt").write_text(
            "one\ntwo\nthree\nfour\n", encoding="utf-8"
        )
        (self.root / ".env").write_text("TOKEN=secret", encoding="utf-8")
        (self.root / "binary.bin").write_bytes(b"abc\x00def")
        self.registry = WorkspaceRegistry({"repo": self.root})
        self.reader = WorkspaceReader(self.registry)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_single_level_list_is_sorted_bounded_and_hides_denied_entries(self) -> None:
        for index in range(205):
            (self.root / f"item-{index:03}.txt").write_text("x", encoding="utf-8")

        result = self.reader.list("repo", "")

        self.assertEqual(len(result["items"]), 200)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["items"], sorted(result["items"], key=lambda item: item["name"]))
        self.assertNotIn(".env", {item["name"] for item in result["items"]})
        self.assertNotIn("binary.bin", {item["name"] for item in result["items"]})
        self.assertFalse(any("notes.txt" == item["name"] for item in result["items"]))

        nested = self.reader.list("repo", "docs")
        self.assertEqual(nested["items"], [{"name": "notes.txt", "type": "file", "size": 19}])

    def test_paged_text_read_has_line_and_utf8_byte_limits(self) -> None:
        page = self.reader.read("repo", "docs/notes.txt", start_line=2, max_lines=2)
        self.assertEqual(page["content"], "two\nthree\n")
        self.assertEqual(page["start_line"], 2)
        self.assertEqual(page["next_start_line"], 4)

        (self.root / "large.txt").write_text("界" * 30_000, encoding="utf-8")
        with self.assertRaisesRegex(ObservationSecurityError, "logical line"):
            self.reader.read("repo", "large.txt", start_line=1, max_lines=200)
        with self.assertRaises(ValueError):
            self.reader.read("repo", "docs/notes.txt", start_line=1, max_lines=201)

        (self.root / "long-line.txt").write_text("x" * 70_000 + "\nsecond\n", encoding="utf-8")
        with self.assertRaisesRegex(ObservationSecurityError, "logical line"):
            self.reader.read("repo", "long-line.txt", start_line=1)
        with self.assertRaisesRegex(ObservationSecurityError, "logical line"):
            self.reader.read("repo", "long-line.txt", start_line=2)

    def test_traversal_absolute_secret_binary_device_and_static_symlink_are_denied(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside.txt"
        outside.write_text("outside", encoding="utf-8")
        self.addCleanup(outside.unlink)
        os.symlink(outside, self.root / "link.txt")
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", "../outside.txt")
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", str(outside))
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", ".env")
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", "binary.bin")
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", "link.txt")
        with self.assertRaises(ObservationSecurityError):
            self.reader.read("repo", "docs//notes.txt")
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.root / "pipe")
            with self.assertRaises(ObservationSecurityError):
                self.reader.read("repo", "pipe")

    def test_descriptor_walk_rejects_path_swap_to_symlink(self) -> None:
        original_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "notes.txt" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                (self.root / "docs" / "notes.txt").unlink()
                os.symlink(self.root / ".env", self.root / "docs" / "notes.txt")
            return original_open(path, flags, *args, **kwargs)

        with patch("backend.main_agent.observations.os.open", side_effect=racing_open):
            with self.assertRaises(ObservationSecurityError):
                self.reader.read("repo", "docs/notes.txt")

    def test_list_sniff_rejects_file_swapped_to_fifo_without_blocking(self) -> None:
        victim = self.root / "victim.txt"
        victim.write_text("text", encoding="utf-8")
        original_open = os.open
        swapped = False

        def racing_open(path, flags, *args, **kwargs):
            nonlocal swapped
            if path == "victim.txt" and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                victim.unlink()
                os.mkfifo(victim)
            return original_open(path, flags, *args, **kwargs)

        with patch("backend.main_agent.observations.os.open", side_effect=racing_open):
            listed = self.reader.list("repo", "")
        self.assertNotIn("victim.txt", {item["name"] for item in listed["items"]})

    def test_secret_components_directory_scan_file_size_and_start_scan_are_bounded(self) -> None:
        secret_paths = [
            ".netrc", ".npmrc", ".pypirc", ".docker/config.json", ".kube/config",
            ".AWS/credentials", "service-account-key.json", "client.keystore",
        ]
        for relative in secret_paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("secret", encoding="utf-8")
            with self.assertRaises(ObservationSecurityError):
                self.reader.read("repo", relative)
        listed = self.reader.list("repo", "")
        names = {item["name"] for item in listed["items"]}
        self.assertTrue(names.isdisjoint({".netrc", ".npmrc", ".pypirc", ".docker", ".kube", ".AWS"}))

        crowded = self.root / "crowded"
        crowded.mkdir()
        for index in range(1_001):
            (crowded / f"item-{index:04}.txt").touch()
        with self.assertRaisesRegex(ObservationSecurityError, "scan limit"):
            self.reader.list("repo", "crowded")

        oversized = self.root / "oversized.txt"
        with oversized.open("wb") as stream:
            stream.truncate(1024 * 1024 + 1)
        with self.assertRaisesRegex(ObservationSecurityError, "1 MiB"):
            self.reader.read("repo", "oversized.txt")
        with self.assertRaisesRegex(ValueError, "start_line"):
            self.reader.read("repo", "docs/notes.txt", start_line=10_001)

        schema = ToolSurfacePolicy(workspace_ids=("repo",)).definition("workspace_read").parameters
        self.assertEqual(schema["properties"]["start_line"]["maximum"], 10_000)

    def test_tool_call_executors_use_only_registered_workspace_arguments(self) -> None:
        listed = asyncio.run(WorkspaceListExecutor(self.reader)(ToolCall(
            "l", "workspace_list", {"workspace_id": "repo", "path": "docs"},
        )))
        read = asyncio.run(WorkspaceReadExecutor(self.reader)(ToolCall(
            "r", "workspace_read", {
                "workspace_id": "repo", "path": "docs/notes.txt", "start_line": 3, "max_lines": 1,
            },
        )))
        self.assertEqual(listed["path"], "docs")
        self.assertEqual(read["content"], "three\n")


if __name__ == "__main__":
    unittest.main()
