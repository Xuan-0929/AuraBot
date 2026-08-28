"""
验证 Task 产品模型与纯状态转换图。

测试只依赖 backend.task.models，不接触 SQLite、进程或 Gateway；任何新增状态和转换都
必须先在这里成为明确的产品决策，再由 Store 与 TaskManager 消费，包括可路由 owner、
受信任 workspace 与创建幂等性。
"""

from __future__ import annotations

from dataclasses import fields
import unittest

from backend.task.models import (
    TERMINAL_TASK_STATUSES,
    TaskErrorCode,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    validate_status_transition,
)


class TaskModelTests(unittest.TestCase):
    def test_task_record_embeds_only_current_question_state(self):
        field_names = {field.name for field in fields(TaskRecord)}

        self.assertTrue(
            {"question_id", "question_text", "question_options", "question_answer"}
            <= field_names
        )
        self.assertTrue(
            {"workspace_id", "owner_profile_id", "idempotency_key"} <= field_names
        )
        self.assertNotIn("pi_session_id", field_names)
        self.assertNotIn("pi_session_path", field_names)

    def test_public_statuses_and_error_codes_are_stable(self):
        self.assertEqual(
            {status.value for status in TaskStatus},
            {
                "queued",
                "running",
                "waiting",
                "paused",
                "completed",
                "failed",
                "cancelled",
            },
        )
        self.assertEqual(
            {code.value for code in TaskErrorCode},
            {
                "spawn_failed",
                "startup_timeout",
                "prompt_rejected",
                "resource_load_failed",
                "protocol_error",
                "unexpected_exit",
                "model_error",
                "timeout",
                "session_missing",
                "result_unavailable",
                "internal_error",
            },
        )
        self.assertEqual(
            TERMINAL_TASK_STATUSES,
            frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}),
        )

    def test_all_approved_status_transitions_are_allowed(self):
        allowed = {
            TaskStatus.QUEUED: {
                TaskStatus.RUNNING,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.RUNNING: {
                TaskStatus.WAITING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.WAITING: {
                TaskStatus.QUEUED,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.PAUSED: {
                TaskStatus.QUEUED,
                TaskStatus.WAITING,
                TaskStatus.CANCELLED,
            },
            TaskStatus.COMPLETED: set(),
            TaskStatus.FAILED: set(),
            TaskStatus.CANCELLED: set(),
        }

        for source in TaskStatus:
            for target in TaskStatus:
                with self.subTest(source=source, target=target):
                    if source == target or target in allowed[source]:
                        self.assertEqual(validate_status_transition(source, target), target)
                    else:
                        with self.assertRaises(TaskStateConflict):
                            validate_status_transition(source, target)
