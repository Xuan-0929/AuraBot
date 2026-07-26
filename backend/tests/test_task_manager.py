"""
验证 TaskManager 的准入、FIFO 调度、中断、保留、事件和关闭语义。

测试使用真实 Task/runtime 组件与内存 Harness，不覆盖 HTTP gateway 或外部 MCP 网络。
"""

import asyncio
import unittest
from dataclasses import FrozenInstanceError

from backend.agent.capabilities.lease import CapabilitySession
from backend.agent.skills.skill_group import SkillGroup
from backend.agent.skills.manager import SkillGroupManager
from backend.agent.task.domain import Task, TaskState, TaskType
from backend.agent.task import (
    DuplicateTaskNameError,
    TaskManager,
)
from backend.agent.task.events import TaskEventBroker
from backend.agent.task.harness import Harness, HarnessFactory
from backend.agent.task.runtime.records import (
    InterruptTaskOutcome,
    TaskFailure,
    TaskSnapshot,
    TaskStartSpec,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.manager import ToolGroupManager
from backend.agent.tools.native.plan import create_plan_tool_group


def make_tool_group(name: str) -> ToolGroup:
    return ToolGroup(
        name,
        [
            Tool(
                f"{name}_tool",
                f"{name} tool",
                "http://127.0.0.1:6410/mcp",
                input_schema={"type": "object", "properties": {}},
            )
        ],
        f"{name} tools",
    )


def capability_session_factory(
    tool_manager: ToolGroupManager,
    skill_manager: SkillGroupManager,
):
    return lambda: CapabilitySession(tool_manager, skill_manager)


class ControlledHarness(Harness):
    def __init__(self, *, release: asyncio.Event, **kwargs):
        self._release = release
        super().__init__(**kwargs)

    def inject_tool_group(self) -> None:
        self._inject_required_tool_groups(())

    def build_system_prompt(self) -> str:
        return "controlled"

    async def run(self) -> str:
        self._transition_task_state(
            TaskState.Initializing,
            TaskState.Thinking,
        )
        await self._release.wait()
        self._transition_task_state(
            TaskState.Thinking,
            TaskState.Completed,
        )
        return f"result:{self.task.task_title}"


class ControlledHarnessFactory:
    def __init__(self):
        self.releases: dict[str, asyncio.Event] = {}
        self.created: list[str] = []

    def create(
        self,
        task: Task,
        *,
        capability_session: CapabilitySession | None = None,
    ) -> ControlledHarness:
        release = asyncio.Event()
        self.releases[task.task_title] = release
        self.created.append(task.task_title)
        return ControlledHarness(
            task=task,
            llm_client=object(),
            capability_session=capability_session,
            release=release,
        )


class FailingHarnessFactory:
    def create(self, task, *, capability_session=None):
        raise RuntimeError("construction failed")


class BlockingLLMClient:
    async def invoke(self, messages, *, tools=None, tool_choice=None):
        await asyncio.Event().wait()


class StubbornHarness(ControlledHarness):
    def __init__(
        self,
        *,
        started: asyncio.Event,
        cancelled: asyncio.Event,
        finish_cancel: asyncio.Event,
        **kwargs,
    ):
        self._started = started
        self._cancelled = cancelled
        self._finish_cancel = finish_cancel
        super().__init__(**kwargs)

    async def run(self) -> str:
        self._transition_task_state(
            TaskState.Initializing,
            TaskState.Thinking,
        )
        self._started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self._cancelled.set()
            await self._finish_cancel.wait()
            raise


class StubbornHarnessFactory:
    def __init__(self):
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.finish_cancel = asyncio.Event()

    def create(self, task, *, capability_session=None):
        return StubbornHarness(
            task=task,
            llm_client=object(),
            capability_session=capability_session,
            release=asyncio.Event(),
            started=self.started,
            cancelled=self.cancelled,
            finish_cancel=self.finish_cancel,
        )


def spec(
    name: str,
    *,
    tool_groups: tuple[str, ...] = (),
    timeout_ms: int | None = None,
) -> TaskStartSpec:
    return TaskStartSpec(
        task_name=name,
        parent_session_id="main",
        task_type=TaskType.ReAct,
        task_prompt=f"Run {name}",
        tool_group_names=tool_groups,
        timeout_ms=timeout_ms,
    )


class TaskManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.factory = ControlledHarnessFactory()
        self.tool_manager = ToolGroupManager(loader=make_tool_group)
        self.skill_manager = SkillGroupManager(
            loader=lambda name: SkillGroup(name, [])
        )
        self.broker = TaskEventBroker(queue_size=32, stream_id="tests")
        self.manager = TaskManager(
            harness_factory=self.factory,
            capability_session_factory=capability_session_factory(
                self.tool_manager,
                self.skill_manager,
            ),
            event_broker=self.broker,
            max_running=1,
            default_timeout_ms=300_000,
            max_timeout_ms=1_800_000,
            retention_seconds=0.02,
        )
        self.manager.bind_event_loop(asyncio.get_running_loop())

    async def asyncTearDown(self):
        await self.manager.aclose(grace_seconds=0)

    async def test_manager_instances_are_owned_by_runtime_not_global_singletons(self):
        other = TaskManager(
            harness_factory=self.factory,
            capability_session_factory=capability_session_factory(
                self.tool_manager,
                self.skill_manager,
            ),
            event_broker=TaskEventBroker(),
        )
        self.assertIsNot(other, self.manager)
        await other.aclose(grace_seconds=0)

    async def test_mutations_are_confined_to_the_bound_event_loop(self):
        async def mutate_from_foreign_loop():
            self.manager.start_task(spec("foreign-loop"))

        with self.assertRaisesRegex(RuntimeError, "another event loop"):
            await asyncio.to_thread(
                lambda: asyncio.run(mutate_from_foreign_loop())
            )

        self.assertIsNone(self.manager.get_task("foreign-loop"))

    async def test_fifo_admission_constructs_capabilities_and_harness_only_on_slot(self):
        first = self.manager.start_task(spec("first", tool_groups=("files",)))
        second = self.manager.start_task(spec("second", tool_groups=("shell",)))
        await asyncio.sleep(0)

        self.assertEqual(self.factory.created, ["first"])
        first = self.manager.get_task("first")
        second = self.manager.get_task("second")
        self.assertIs(first.state, TaskState.Thinking)
        self.assertIs(second.state, TaskState.Initializing)
        self.assertEqual(second.queue_position, 1)
        self.assertEqual(self.tool_manager.reference_count("files"), 1)
        self.assertEqual(self.tool_manager.reference_count("shell"), 0)

        self.factory.releases["first"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(self.factory.created, ["first", "second"])
        second = self.manager.get_task("second")
        self.assertIs(second.state, TaskState.Thinking)
        self.assertEqual(self.tool_manager.reference_count("files"), 1)
        self.assertEqual(self.tool_manager.reference_count("shell"), 1)

    async def test_start_returns_immutable_public_snapshot(self):
        snapshot = self.manager.start_task(spec("snapshot"))

        self.assertIsInstance(snapshot, TaskSnapshot)
        self.assertEqual(snapshot.task_name, "snapshot")
        self.assertFalse(hasattr(snapshot, "runner"))
        self.assertFalse(hasattr(snapshot, "harness"))
        with self.assertRaises(FrozenInstanceError):
            snapshot.state = TaskState.Failed

    async def test_duplicate_name_is_rejected_during_retention(self):
        self.manager.start_task(spec("same"))

        with self.assertRaises(DuplicateTaskNameError):
            self.manager.start_task(spec("same"))

    async def test_interrupting_queued_task_is_immediate_and_never_builds_harness(self):
        self.manager.start_task(spec("running"))
        queued = self.manager.start_task(spec("queued"))
        await asyncio.sleep(0)

        result = self.manager.interrupt_task("queued")

        self.assertIs(result.outcome, InterruptTaskOutcome.Interrupted)
        queued = self.manager.get_task("queued")
        self.assertIs(queued.state, TaskState.Interrupted)
        self.assertNotIn("queued", self.factory.created)
        self.assertEqual(queued.queue_position, None)

    async def test_runner_result_is_retained_then_capability_is_released(self):
        self.manager.start_task(spec("retained", tool_groups=("files",)))
        await asyncio.sleep(0)
        self.factory.releases["retained"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        snapshot = self.manager.get_task("retained")
        self.assertIs(snapshot.state, TaskState.Completed)
        self.assertEqual(snapshot.result, "result:retained")
        self.assertEqual(self.tool_manager.reference_count("files"), 1)

        await asyncio.sleep(0.04)

        self.assertIsNone(self.manager.get_task("retained"))
        self.assertEqual(self.tool_manager.reference_count("files"), 0)

    async def test_clear_rejects_active_and_releases_terminal_runtime(self):
        self.manager.start_task(spec("clear", tool_groups=("files",)))
        await asyncio.sleep(0)
        self.assertFalse(self.manager.clear_task("clear"))

        self.factory.releases["clear"].set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertTrue(self.manager.clear_task("clear"))
        self.assertIsNone(self.manager.get_task("clear"))
        self.assertEqual(self.tool_manager.reference_count("files"), 0)

    async def test_timeout_is_validated_and_starts_with_actual_execution(self):
        self.manager.start_task(spec("running"))
        self.manager.start_task(
            spec("short-timeout", timeout_ms=10)
        )
        await asyncio.sleep(0.03)

        self.assertIs(
            self.manager.get_task("running").state,
            TaskState.Thinking,
        )
        self.assertIs(
            self.manager.get_task("short-timeout").state,
            TaskState.Initializing,
        )

        self.factory.releases["running"].set()
        for _ in range(20):
            queued = self.manager.get_task("short-timeout")
            if queued.state is TaskState.Failed:
                break
            await asyncio.sleep(0.005)

        self.assertIs(queued.state, TaskState.Failed)
        self.assertEqual(
            queued.error,
            TaskFailure(
                code="task_execution_failed",
                message="Task execution failed.",
            ),
        )

        with self.assertRaisesRegex(ValueError, "timeout"):
            self.manager.start_task(spec("too-long", timeout_ms=1_800_001))

    async def test_real_harness_timeouts_settle_as_failed_not_interrupted(self):
        tool_manager = ToolGroupManager(
            loader=make_tool_group,
            native_factories={
                "Plan": lambda: create_plan_tool_group(
                    "http://127.0.0.1:6410/mcp"
                )
            },
        )
        manager = TaskManager(
            harness_factory=HarnessFactory(
                task_llm_client=BlockingLLMClient()
            ),
            capability_session_factory=capability_session_factory(
                tool_manager,
                SkillGroupManager(
                    loader=lambda name: SkillGroup(name, [])
                ),
            ),
            event_broker=TaskEventBroker(),
            max_running=2,
            default_timeout_ms=10,
            max_timeout_ms=100,
        )
        manager.bind_event_loop(asyncio.get_running_loop())
        task_names = [
            task_type.value
            for task_type in (TaskType.ReAct, TaskType.PlanAndSolve)
        ]
        for task_type in (TaskType.ReAct, TaskType.PlanAndSolve):
            manager.start_task(
                TaskStartSpec(
                    task_name=task_type.value,
                    parent_session_id="main",
                    task_type=task_type,
                    task_prompt="wait forever",
                    timeout_ms=10,
                )
            )

        for _ in range(30):
            if all(
                manager.get_task(task_name).state is TaskState.Failed
                for task_name in task_names
            ):
                break
            await asyncio.sleep(0.005)

        self.assertEqual(
            [
                manager.get_task(task_name).state
                for task_name in task_names
            ],
            [TaskState.Failed, TaskState.Failed],
        )
        self.assertTrue(
            all(
                manager.get_task(task_name).error
                == TaskFailure(
                    code="task_execution_failed",
                    message="Task execution failed.",
                )
                for task_name in task_names
            )
        )
        await manager.aclose(grace_seconds=0)

    async def test_list_and_counts_cover_running_queued_and_retained_tasks(self):
        self.manager.start_task(spec("running"))
        self.manager.start_task(spec("queued"))
        await asyncio.sleep(0)

        self.assertEqual(self.manager.running_count, 1)
        self.assertEqual(self.manager.queued_count, 1)
        self.assertEqual(
            tuple(
                snapshot.task_name
                for snapshot in self.manager.list_tasks().tasks
            ),
            ("running", "queued"),
        )

    async def test_partial_capability_acquisition_is_rolled_back_on_skill_failure(self):
        tool_manager = ToolGroupManager(loader=make_tool_group)
        manager = TaskManager(
            harness_factory=self.factory,
            capability_session_factory=capability_session_factory(
                tool_manager,
                SkillGroupManager(loader=lambda name: None),
            ),
            event_broker=TaskEventBroker(),
        )
        manager.bind_event_loop(asyncio.get_running_loop())

        snapshot = manager.start_task(
            TaskStartSpec(
                task_name="missing-skill",
                parent_session_id="main",
                task_type=TaskType.ReAct,
                task_prompt="work",
                tool_group_names=("files",),
                skill_group_names=("missing",),
            )
        )

        self.assertIs(snapshot.state, TaskState.Failed)
        self.assertEqual(tool_manager.reference_count("files"), 0)
        await manager.aclose(grace_seconds=0)

    async def test_capabilities_are_rolled_back_when_harness_construction_fails(self):
        tool_manager = ToolGroupManager(loader=make_tool_group)
        skill_manager = SkillGroupManager(
            loader=lambda name: SkillGroup(name, [])
        )
        manager = TaskManager(
            harness_factory=FailingHarnessFactory(),
            capability_session_factory=capability_session_factory(
                tool_manager,
                skill_manager,
            ),
            event_broker=TaskEventBroker(),
        )
        manager.bind_event_loop(asyncio.get_running_loop())

        snapshot = manager.start_task(
            TaskStartSpec(
                task_name="bad-harness",
                parent_session_id="main",
                task_type=TaskType.ReAct,
                task_prompt="work",
                tool_group_names=("files",),
                skill_group_names=("research",),
            )
        )

        self.assertIs(snapshot.state, TaskState.Failed)
        self.assertEqual(tool_manager.reference_count("files"), 0)
        self.assertEqual(skill_manager.reference_count("research"), 0)
        await manager.aclose(grace_seconds=0)

    async def test_shutdown_survives_caller_cancellation_and_emits_closing_event(self):
        subscription = self.manager.subscribe_events()
        await subscription.next_event()
        self.manager.start_task(spec("shutdown", tool_groups=("files",)))
        await asyncio.sleep(0)

        closing = asyncio.create_task(
            self.manager.aclose(grace_seconds=0)
        )
        closing.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await closing

        await self.manager.aclose(grace_seconds=0)

        events = []
        while True:
            try:
                events.append(await subscription.next_event())
            except StopAsyncIteration:
                break
        self.assertIn("runtime.closing", [event["event"] for event in events])
        self.assertEqual(self.manager.list_tasks().tasks, ())
        self.assertEqual(self.tool_manager.reference_count("files"), 0)

    async def test_in_progress_shutdown_continues_when_awaiting_caller_is_cancelled(self):
        factory = StubbornHarnessFactory()
        manager = TaskManager(
            harness_factory=factory,
            capability_session_factory=capability_session_factory(
                ToolGroupManager(loader=make_tool_group),
                SkillGroupManager(
                    loader=lambda name: SkillGroup(name, [])
                ),
            ),
            event_broker=TaskEventBroker(),
        )
        manager.bind_event_loop(asyncio.get_running_loop())
        manager.start_task(spec("stubborn"))
        await factory.started.wait()
        caller = asyncio.create_task(manager.aclose(grace_seconds=1))
        await factory.cancelled.wait()

        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertNotEqual(manager.list_tasks().tasks, ())

        factory.finish_cancel.set()
        await manager.aclose(grace_seconds=1)
        self.assertEqual(manager.list_tasks().tasks, ())


if __name__ == "__main__":
    unittest.main()
