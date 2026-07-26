"""
验证 PlanContextAdaptor 安全注入 Task 名称并保持模型参数不变。

测试直接观察 ResolvedToolCall 副本与错误语义，不执行 MCP 请求或 Plan 领域操作。
"""

import unittest

from backend.agent.task.domain import Task, TaskType
from backend.agent.task.harness.adaptors.base import AdaptorRouteError
from backend.agent.task.harness.adaptors.plan_context import (
    PlanContextAdaptor,
)
from backend.agent.tools.tool import Tool
from backend.agent.tools.core.model import ResolvedToolCall


def make_call(meta=None):
    return ResolvedToolCall(
        tool=Tool(
            "create_plan",
            "Create",
            "http://127.0.0.1:6410/mcp",
            input_schema={"type": "object", "properties": {}},
        ),
        arguments={"goal": "Ship"},
        call_id="call_1",
        mcp_meta=meta or {},
    )


class PlanContextAdaptorTests(unittest.TestCase):
    def make_task(self, title="Unique task"):
        return Task(1, title, "session", TaskType.PlanAndSolve, "Ship")

    def test_injects_task_name_without_changing_llm_fields(self):
        task = self.make_task()
        call = make_call({"trace": {"id": "trace-1"}})

        adapted = PlanContextAdaptor(task).adapt(call)

        self.assertIs(adapted.tool, call.tool)
        self.assertIs(adapted.arguments, call.arguments)
        self.assertEqual(adapted.call_id, call.call_id)
        self.assertEqual(
            adapted.mcp_meta,
            {
                "trace": {"id": "trace-1"},
                "aurabot": {"task_name": "Unique task"},
            },
        )
        self.assertEqual(call.mcp_meta, {"trace": {"id": "trace-1"}})

    def test_preserves_existing_aurabot_fields(self):
        adapted = PlanContextAdaptor(self.make_task()).adapt(
            make_call({"aurabot": {"request_id": "r1"}})
        )
        self.assertEqual(
            adapted.mcp_meta["aurabot"],
            {"request_id": "r1", "task_name": "Unique task"},
        )

    def test_rejects_invalid_task_names_and_namespace_types(self):
        for title in ("", " padded "):
            with self.subTest(title=title):
                with self.assertRaises(AdaptorRouteError):
                    PlanContextAdaptor(self.make_task(title)).adapt(make_call())
        with self.assertRaises(AdaptorRouteError):
            PlanContextAdaptor(self.make_task()).adapt(
                make_call({"aurabot": "invalid"})
            )


if __name__ == "__main__":
    unittest.main()
