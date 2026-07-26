"""
验证 Task 应用服务、TaskManager 与 HarnessFactory 的组合运行行为。

测试覆盖不同 TaskType、能力组注入和应用查询，不负责 HTTP 序列化或真实外部服务。
"""

import asyncio
import unittest

from backend.agent.llm_client import ChatCompletionChoice
from backend.agent.capabilities.lease import CapabilitySession
from backend.agent.skills.manager import SkillGroupManager
from backend.agent.task.domain import TaskState, TaskType
from backend.agent.task.manager import TaskManager
from backend.agent.task.events import TaskEventBroker
from backend.agent.task.harness.factory import HarnessFactory
from backend.agent.task.runtime import TaskStartSpec
from backend.agent.tools.manager import ToolGroupManager
from backend.agent.tools.native.plan import create_plan_tool_group
from backend.app.services.task_service import TaskApplicationService


class TaskTypeAwareLLM:
    async def invoke(self, messages, *, tools=None, tool_choice=None):
        del messages, tool_choice
        if tools:
            content = (
                "Status: failed\n"
                "Summary: Deliberate test stop\n"
                "Artifacts: none\n"
                "Verification: runtime activated\n"
                "Blocker: test stop"
            )
        else:
            content = "ReAct finished"
        return ChatCompletionChoice(
            message={"role": "assistant", "content": content},
            finish_reason="stop",
        )


class TaskRuntimeApplicationTests(unittest.IsolatedAsyncioTestCase):
    async def test_application_service_runs_react_and_plan_harness_lifecycles(self):
        tool_manager = ToolGroupManager(
            loader=lambda name: None,
            native_factories={
                "Plan": lambda: create_plan_tool_group(
                    "http://127.0.0.1:6410/mcp"
                )
            },
        )
        manager = TaskManager(
            harness_factory=HarnessFactory(
                task_llm_client=TaskTypeAwareLLM()
            ),
            capability_session_factory=lambda: CapabilitySession(
                tool_manager,
                SkillGroupManager(loader=lambda name: None),
            ),
            event_broker=TaskEventBroker(),
            max_running=2,
            retention_seconds=100,
        )
        manager.bind_event_loop(asyncio.get_running_loop())
        service = TaskApplicationService(manager)

        service.start(
            TaskStartSpec(
                task_name="react-runtime",
                parent_session_id="main",
                task_type=TaskType.ReAct,
                task_prompt="Finish",
            )
        )
        service.start(
            TaskStartSpec(
                task_name="plan-runtime",
                parent_session_id="main",
                task_type=TaskType.PlanAndSolve,
                task_prompt="Plan",
            )
        )

        for _ in range(20):
            snapshots = manager.list_tasks().tasks
            if all(
                snapshot.state
                in {
                    TaskState.Completed,
                    TaskState.Failed,
                    TaskState.Interrupted,
                }
                for snapshot in snapshots
            ):
                break
            await asyncio.sleep(0)

        react = manager.get_task("react-runtime")
        plan = manager.get_task("plan-runtime")
        self.assertIsNotNone(react)
        self.assertIsNotNone(plan)
        self.assertIs(react.state, TaskState.Completed)
        self.assertEqual(react.result, "ReAct finished")
        self.assertIs(plan.state, TaskState.Failed)
        self.assertTrue(plan.result.startswith("Status: failed"))
        self.assertEqual(tool_manager.reference_count("Plan"), 1)

        self.assertTrue(manager.clear_task("react-runtime"))
        self.assertTrue(manager.clear_task("plan-runtime"))
        self.assertEqual(tool_manager.reference_count("Plan"), 0)
        await manager.aclose(grace_seconds=0)


if __name__ == "__main__":
    unittest.main()
