import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.agent.task.Task import Task
from backend.agent.task.TaskEntity import (
    InterruptTaskOutcome,
    TaskState,
    TaskType,
)
from backend.agent.task.TaskManager import TaskManager


class TaskManagerSingletonTests(unittest.TestCase):
    def setUp(self):
        if hasattr(TaskManager, "_instance"):
            TaskManager._instance = None

    def test_task_manager_returns_the_same_instance(self):
        first = TaskManager()
        second = TaskManager()

        self.assertIs(second, first)

    def test_task_manager_initializes_only_once(self):
        first = TaskManager()
        first.create_task("task-1", "reminder", {"message": "standup"})

        second = TaskManager()

        self.assertEqual(
            second.list_tasks(),
            [
                {
                    "id": "task-1",
                    "type": "reminder",
                    "data": {"message": "standup"},
                }
            ],
        )


class TaskManagerControlTests(unittest.TestCase):
    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def make_task(
        self,
        *,
        task_id: int = 1,
        task_title: str = "Inspect repository",
        state: TaskState = TaskState.Thinking,
    ) -> Task:
        task = Task(
            task_id=task_id,
            task_title=task_title,
            parent_session_id="main-session",
            task_type=TaskType.ReAct,
            task_prompt="Inspect the repository.",
        )
        task.current_state = state
        self.manager._tasks[str(task_id)] = task
        return task

    def test_peek_returns_a_read_only_task_snapshot(self):
        task = self.make_task()
        task.checkpoint = "Read configuration"

        result = self.manager.peek_task(task.task_title)

        self.assertTrue(result.found)
        self.assertEqual(result.task_id, task.task_id)
        self.assertIs(result.task_type, TaskType.ReAct)
        self.assertIs(result.state, TaskState.Thinking)
        self.assertEqual(result.checkpoint, "Read configuration")

    def test_peek_reports_a_missing_task(self):
        result = self.manager.peek_task("missing")

        self.assertFalse(result.found)
        self.assertIsNone(result.task_id)
        self.assertIsNone(result.state)

    def test_interrupt_atomically_changes_an_active_task(self):
        task = self.make_task()

        result = self.manager.interrupt_task(task.task_title)

        self.assertIs(result.outcome, InterruptTaskOutcome.Interrupted)
        self.assertIs(result.previous_state, TaskState.Thinking)
        self.assertIs(result.state, TaskState.Interrupted)
        self.assertIs(task.state, TaskState.Interrupted)

    def test_interrupt_is_idempotent(self):
        task = self.make_task(state=TaskState.Interrupted)

        result = self.manager.interrupt_task(task.task_title)

        self.assertIs(result.outcome, InterruptTaskOutcome.AlreadyInterrupted)
        self.assertIs(result.state, TaskState.Interrupted)

    def test_interrupt_is_deferred_during_atomic_initialization(self):
        task = self.make_task(state=TaskState.Initializing)

        result = self.manager.interrupt_task(task.task_title)

        self.assertIs(result.outcome, InterruptTaskOutcome.Deferred)
        self.assertIs(result.previous_state, TaskState.Initializing)
        self.assertIs(result.state, TaskState.Initializing)
        self.assertFalse(
            task.compare_and_set_state(
                TaskState.Initializing,
                TaskState.Thinking,
            )
        )
        self.assertIs(task.state, TaskState.Interrupted)

    def test_interrupt_does_not_overwrite_a_settled_task(self):
        for index, state in enumerate(
            (TaskState.Completed, TaskState.Failed),
            start=1,
        ):
            task = self.make_task(
                task_id=index,
                task_title=f"settled-{index}",
                state=state,
            )

            result = self.manager.interrupt_task(task.task_title)

            self.assertIs(result.outcome, InterruptTaskOutcome.AlreadySettled)
            self.assertIs(result.previous_state, state)
            self.assertIs(result.state, state)
            self.assertIs(task.state, state)

    def test_interrupt_reports_a_missing_task(self):
        result = self.manager.interrupt_task("missing")

        self.assertIs(result.outcome, InterruptTaskOutcome.NotFound)
        self.assertIsNone(result.task_id)
        self.assertIsNone(result.state)

    def test_concurrent_interrupts_have_one_state_change(self):
        task = self.make_task()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    self.manager.interrupt_task,
                    [task.task_title] * 4,
                )
            )

        outcomes = [result.outcome for result in results]
        self.assertEqual(outcomes.count(InterruptTaskOutcome.Interrupted), 1)
        self.assertEqual(
            outcomes.count(InterruptTaskOutcome.AlreadyInterrupted),
            3,
        )

    def test_late_harness_result_cannot_overwrite_interrupt(self):
        task = self.make_task()

        self.manager.interrupt_task(task.task_title)
        task.compare_and_set_state(
            TaskState.Thinking,
            TaskState.Completed,
        )
        task.compare_and_set_state(
            TaskState.Thinking,
            TaskState.Failed,
        )

        self.assertIs(task.state, TaskState.Interrupted)

    def test_lookup_ignores_legacy_dictionary_entries(self):
        self.manager.create_task(
            "legacy",
            "reminder",
            {"task_title": "Inspect repository"},
        )

        result = self.manager.peek_task("Inspect repository")

        self.assertFalse(result.found)


if __name__ == "__main__":
    unittest.main()
