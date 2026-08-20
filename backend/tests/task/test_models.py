"""
验证 Task 产品模型与纯状态转换图。

测试只依赖 backend.task.models，不接触 SQLite、进程或 Gateway；任何新增状态和转换都
必须先在这里成为明确的产品决策，再由 Store 与 TaskManager 消费。
"""

from __future__ import annotations

import unittest

from backend.task.models import (
    TERMINAL_TASK_STATUSES,
    TaskErrorCode,
    TaskStateConflict,
    TaskStatus,
    validate_status_transition,
)


class TaskModelTests(unittest.TestCase):
    def test_public_statuses_and_error_codes_are_stable(self):
        self.assertEqual(
            {status.value for status in TaskStatus},
            {
                "queued",
                "starting",
                "running",
                "waiting",
                "pausing",
                "paused",
                "cancelling",
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
                TaskStatus.STARTING,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.STARTING: {
                TaskStatus.RUNNING,
                TaskStatus.PAUSING,
                TaskStatus.CANCELLING,
                TaskStatus.FAILED,
                TaskStatus.PAUSED,
            },
            TaskStatus.RUNNING: {
                TaskStatus.WAITING,
                TaskStatus.PAUSING,
                TaskStatus.CANCELLING,
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.PAUSED,
            },
            TaskStatus.WAITING: {
                TaskStatus.RUNNING,
                TaskStatus.PAUSING,
                TaskStatus.CANCELLING,
                TaskStatus.QUEUED,
                TaskStatus.PAUSED,
                TaskStatus.CANCELLED,
            },
            TaskStatus.PAUSING: {TaskStatus.PAUSED, TaskStatus.FAILED},
            TaskStatus.PAUSED: {
                TaskStatus.QUEUED,
                TaskStatus.WAITING,
                TaskStatus.CANCELLED,
            },
            TaskStatus.CANCELLING: {TaskStatus.CANCELLED, TaskStatus.FAILED},
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


if __name__ == "__main__":
    unittest.main()
