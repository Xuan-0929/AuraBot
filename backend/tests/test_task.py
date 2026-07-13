import unittest

from backend.agent.task.Task import TaskType


class TaskTypeTests(unittest.TestCase):
    def test_task_type_defines_supported_agent_modes(self):
        self.assertEqual(TaskType.ReAct.value, "ReAct")
        self.assertEqual(TaskType.PlanAndSave.value, "PlanAndSave")


if __name__ == "__main__":
    unittest.main()
