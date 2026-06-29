import unittest

from agent.task.TaskManager import TaskManager


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


if __name__ == "__main__":
    unittest.main()
