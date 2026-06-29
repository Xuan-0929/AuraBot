import unittest
from pathlib import Path


class TaskTypeTests(unittest.TestCase):
    def test_task_type_defines_supported_agent_modes(self):
        source = Path("agent/task/Task.py").read_text()
        enum_source = source.split("class Task:", maxsplit=1)[0]
        namespace = {}

        exec(enum_source, namespace)

        task_type = namespace["TaskType"]
        self.assertEqual(task_type.ReAct.value, "ReAct")
        self.assertEqual(task_type.PlanAndSave.value, "PlanAndSave")


if __name__ == "__main__":
    unittest.main()
