import asyncio
import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.agent.task.Task import Task
from backend.agent.task.TaskEntity import (
    InterruptTaskOutcome,
    TaskState,
    TaskType,
)
from backend.agent.task.TaskManager import TaskManager
from backend.agent.task.harness.ReActHarness import ReActHarness


class FakeLLMClient:
    pass


def make_runtime(
    *,
    task_id: int = 1,
    task_title: str = "Inspect repository",
    state: TaskState = TaskState.Thinking,
) -> ReActHarness:
    task = Task(
        task_id=task_id,
        task_title=task_title,
        parent_session_id="main-session",
        task_type=TaskType.ReAct,
        task_prompt="Inspect the repository.",
    )
    task.current_state = state
    return ReActHarness(task=task, llm_client=FakeLLMClient())


class TaskManagerTests(unittest.TestCase):
    def setUp(self):
        TaskManager._instance = None
        self.manager = TaskManager()

    def tearDown(self):
        self.manager.shutdown()
        TaskManager._instance = None

    def test_singleton_and_runtime_indexes(self):
        runtime = make_runtime()
        self.manager.register_runtime(runtime)

        self.assertIs(TaskManager(), self.manager)
        self.assertIs(
            self.manager.get_runtime(runtime.task.task_title),
            runtime,
        )
        self.assertEqual(self.manager.list_runtimes(), (runtime,))

    def test_registration_rejects_duplicate_id_and_name(self):
        first = make_runtime()
        self.manager.register_runtime(first)

        with self.assertRaisesRegex(ValueError, "ID"):
            self.manager.register_runtime(
                make_runtime(task_id=1, task_title="Other")
            )
        with self.assertRaisesRegex(ValueError, "name"):
            self.manager.register_runtime(
                make_runtime(task_id=2, task_title=first.task.task_title)
            )
        with self.assertRaises(TypeError):
            self.manager.register_runtime(object())

    def test_peek_reads_task_through_harness(self):
        runtime = make_runtime()
        runtime.task.checkpoint = "Read configuration"
        self.manager.register_runtime(runtime)

        result = self.manager.peek_task(runtime.task.task_title)

        self.assertTrue(result.found)
        self.assertEqual(result.task_id, runtime.task.task_id)
        self.assertIs(result.task_type, TaskType.ReAct)
        self.assertIs(result.state, TaskState.Thinking)
        self.assertEqual(result.checkpoint, "Read configuration")
        self.assertFalse(self.manager.peek_task("missing").found)

    def test_interrupt_atomically_changes_an_active_task(self):
        runtime = make_runtime()
        self.manager.register_runtime(runtime)

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(
                executor.map(
                    self.manager.interrupt_task,
                    [runtime.task.task_title] * 4,
                )
            )

        outcomes = [result.outcome for result in results]
        self.assertEqual(outcomes.count(InterruptTaskOutcome.Interrupted), 1)
        self.assertEqual(
            outcomes.count(InterruptTaskOutcome.AlreadyInterrupted),
            3,
        )
        self.assertIs(runtime.task.state, TaskState.Interrupted)

    def test_interrupt_deferred_settled_and_missing_outcomes(self):
        initializing = make_runtime(state=TaskState.Initializing)
        self.manager.register_runtime(initializing)
        deferred = self.manager.interrupt_task(initializing.task.task_title)
        self.assertIs(deferred.outcome, InterruptTaskOutcome.Deferred)

        self.manager.shutdown()
        settled = make_runtime(
            task_id=2,
            task_title="settled",
            state=TaskState.Completed,
        )
        self.manager.register_runtime(settled)
        result = self.manager.interrupt_task("settled")
        self.assertIs(result.outcome, InterruptTaskOutcome.AlreadySettled)
        self.assertIs(
            self.manager.interrupt_task("missing").outcome,
            InterruptTaskOutcome.NotFound,
        )

    def test_context_removes_nonterminal_runtime_on_normal_or_error_exit(self):
        normal = make_runtime()
        with self.manager.runtime(normal):
            self.assertIsNotNone(self.manager.get_runtime(normal.task.task_title))
        self.assertIsNone(self.manager.get_runtime(normal.task.task_title))

        failing = make_runtime(task_id=2, task_title="failing")
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with self.manager.runtime(failing):
                raise RuntimeError("boom")
        self.assertIsNone(self.manager.get_runtime("failing"))

    def test_clear_only_removes_terminal_runtime(self):
        active = make_runtime()
        self.manager.register_runtime(active)
        self.assertFalse(self.manager.clear_task(active.task.task_title))

        active.task.current_state = TaskState.Failed
        self.assertTrue(self.manager.clear_task(active.task.task_title))
        self.assertFalse(self.manager.clear_task(active.task.task_title))

    def test_old_expiry_callback_cannot_remove_new_runtime(self):
        old = make_runtime(state=TaskState.Completed)
        self.manager.register_runtime(old)
        self.assertTrue(self.manager.clear_task(old.task.task_title))
        replacement = make_runtime(task_id=2)
        self.manager.register_runtime(replacement)

        self.manager._expire_runtime(replacement.task.task_title, old)

        self.assertIs(
            self.manager.get_runtime(replacement.task.task_title),
            replacement,
        )


class TaskManagerRetentionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        TaskManager._instance = None
        self.manager = TaskManager(retention_seconds=0.01)
        self.manager.bind_event_loop(asyncio.get_running_loop())

    async def asyncTearDown(self):
        self.manager.shutdown()
        TaskManager._instance = None

    async def test_all_terminal_states_are_retained_then_expire(self):
        for index, state in enumerate(
            (
                TaskState.Completed,
                TaskState.Failed,
                TaskState.Interrupted,
            ),
            start=1,
        ):
            runtime = make_runtime(
                task_id=index,
                task_title=f"terminal-{index}",
                state=state,
            )
            with self.manager.runtime(runtime):
                pass
            self.assertIs(
                self.manager.get_runtime(runtime.task.task_title),
                runtime,
            )

        await asyncio.sleep(0.03)
        self.assertEqual(self.manager.list_runtimes(), ())

    async def test_shutdown_cancels_timers_and_clears_runtimes(self):
        runtime = make_runtime(state=TaskState.Completed)
        with self.manager.runtime(runtime):
            pass
        await asyncio.sleep(0)
        self.assertTrue(self.manager._cleanup_handles)

        self.manager.shutdown()

        self.assertEqual(self.manager.list_runtimes(), ())
        self.assertEqual(self.manager._cleanup_handles, {})


if __name__ == "__main__":
    unittest.main()
