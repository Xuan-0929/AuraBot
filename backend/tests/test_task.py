import unittest
from concurrent.futures import ThreadPoolExecutor

from backend.agent.skills.skill_group import SkillGroup
from backend.agent.task.model import Task, TaskState, TaskType
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.core.model import ToolCall
from backend.agent.tools.core.errors import (
    DuplicateToolNameError,
    ToolNotAuthorizedError,
)


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class TaskTypeTests(unittest.TestCase):
    def test_task_type_defines_supported_agent_modes(self):
        self.assertEqual(TaskType.ReAct.value, "ReAct")
        self.assertEqual(TaskType.PlanAndSolve.value, "PlanAndSolve")


class TaskTests(unittest.TestCase):
    def test_task_has_no_plan_or_goal_specialization(self):
        for task_type in (TaskType.ReAct, TaskType.PlanAndSolve, TaskType.Goal):
            task = Task(1, "Task", "session", task_type, "Work")
            self.assertFalse(hasattr(task, "plan"))
            self.assertFalse(hasattr(task, "goal"))

    def test_state_cas_does_not_encode_one_harness_state_graph(self):
        task = Task(
            1,
            "Plan",
            "session-1",
            TaskType.PlanAndSolve,
            "Plan and build.",
        )

        transitioned = task.compare_and_set_state(
            TaskState.Initializing,
            TaskState.Planning,
        )

        self.assertTrue(transitioned)
        self.assertIs(task.state, TaskState.Planning)

    def test_state_cas_allows_only_one_concurrent_writer(self):
        task = Task(
            1,
            "Concurrent",
            "session-1",
            TaskType.ReAct,
            "Run once.",
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda target: task.compare_and_set_state(
                        TaskState.Initializing,
                        target,
                    ),
                    (TaskState.Thinking, TaskState.Planning),
                )
            )

        self.assertEqual(results.count(True), 1)
        self.assertIn(
            task.state,
            {TaskState.Thinking, TaskState.Planning},
        )

    def test_state_observers_receive_committed_transitions_outside_state_lock(self):
        task = Task(1, "Observed", "session", TaskType.ReAct, "Work")
        transitions = []

        def observer(observed, previous, current):
            self.assertIs(observed, task)
            with observed.active_state_guard():
                transitions.append((previous, current))

        unsubscribe = task.add_state_observer(observer)
        task.compare_and_set_state(TaskState.Initializing, TaskState.Thinking)
        unsubscribe()
        task.compare_and_set_state(TaskState.Thinking, TaskState.Pushing)

        self.assertEqual(
            transitions,
            [(TaskState.Initializing, TaskState.Thinking)],
        )

    def test_interrupting_unstarted_initializing_task_is_immediate(self):
        task = Task(1, "Queued", "session", TaskType.ReAct, "Work")

        previous, current = task.interrupt()

        self.assertIs(previous, TaskState.Initializing)
        self.assertIs(current, TaskState.Interrupted)

    def test_message_history_is_mutated_only_through_task_operations(self):
        task = Task(1, "Chat", "session", TaskType.ReAct, "Work")

        task.initialize_messages(
            (
                {"role": "system", "content": "System"},
                {"role": "user", "content": "Work"},
            )
        )
        first_snapshot = task.messages
        task.append_message({"role": "assistant", "content": "Done"})

        self.assertIsInstance(first_snapshot, tuple)
        self.assertEqual(len(first_snapshot), 2)
        self.assertEqual(len(task.messages), 3)
        self.assertIsNot(first_snapshot, task.messages)

    def test_constructor_accepts_multiple_skill_and_tool_groups(self):
        skill_groups = [
            SkillGroup("research", []),
            SkillGroup("writing", []),
        ]
        tool_groups = [
            ToolGroup(
                "web",
                [
                    Tool(
                        "search",
                        "Search the web",
                        "localhost:6410",
                        input_schema=EMPTY_INPUT_SCHEMA,
                    )
                ],
                "Web tools",
            ),
            ToolGroup(
                "files",
                [
                    Tool(
                        "read",
                        "Read a file",
                        "localhost:6410",
                        input_schema=EMPTY_INPUT_SCHEMA,
                    )
                ],
                "File tools",
            ),
        ]

        task = Task(
            task_id=1,
            task_title="Prepare report",
            parent_session_id="session-1",
            task_type=TaskType.ReAct,
            task_prompt="Research and write a report.",
            skill_groups=skill_groups,
            tool_groups=tool_groups,
        )

        self.assertEqual(task.skill_groups, tuple(skill_groups))
        self.assertEqual(task.tool_groups, tuple(tool_groups))
        self.assertIsNotNone(task.tool_call_resolver)

    def test_constructor_creates_independent_group_state(self):
        first = Task(1, "First", "session-1", TaskType.ReAct, "First task")
        second = Task(2, "Second", "session-1", TaskType.ReAct, "Second task")

        first.update_skill_groups((SkillGroup("research", []),))
        first.update_tool_groups(
            [
                ToolGroup(
                    "web",
                    [
                        Tool(
                            "search",
                            "Search the web",
                            "localhost:6410",
                            input_schema=EMPTY_INPUT_SCHEMA,
                        )
                    ],
                    "Web tools",
                )
            ]
        )

        self.assertEqual(second.skill_groups, ())
        self.assertEqual(second.tool_groups, ())

    def test_empty_tool_group_creates_an_empty_authorization_index(self):
        empty_group = ToolGroup("empty", [], "No tools")

        task = Task(
            1,
            "Empty",
            "session-1",
            TaskType.ReAct,
            "No tools",
            tool_groups=[empty_group],
        )

        self.assertEqual(task.tool_groups, (empty_group,))
        self.assertEqual(task.tool_call_resolver.resolve([]), [])
        with self.assertRaises(ToolNotAuthorizedError):
            task.tool_call_resolver.resolve(
                [ToolCall("missing", {}, "call_1")]
            )

    def test_update_tool_groups_replaces_resolver_and_permissions_together(self):
        read = Tool(
            "read",
            "Read a file",
            "localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        task = Task(1, "Read", "session-1", TaskType.ReAct, "Read a file")
        old_resolver = task.tool_call_resolver

        task.update_tool_groups([ToolGroup("files", [read], "File tools")])

        self.assertIsNot(task.tool_call_resolver, old_resolver)
        resolved = task.tool_call_resolver.resolve(
            [ToolCall("read", {}, "call_1")]
        )
        self.assertIs(resolved[0].tool, read)

    def test_successful_update_revokes_old_tool_and_authorizes_new_tool(self):
        read = Tool(
            "read",
            "Read a file",
            "localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        write = Tool(
            "write",
            "Write a file",
            "localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        task = Task(
            1,
            "Files",
            "session-1",
            TaskType.ReAct,
            "Use file tools",
            tool_groups=[ToolGroup("readers", [read], "Read tools")],
        )

        task.update_tool_groups([ToolGroup("writers", [write], "Write tools")])

        with self.assertRaises(ToolNotAuthorizedError):
            task.tool_call_resolver.resolve([ToolCall("read", {}, "call_1")])
        self.assertIs(
            task.tool_call_resolver.resolve(
                [ToolCall("write", {}, "call_2")]
            )[0].tool,
            write,
        )

    def test_input_group_list_mutation_does_not_change_task_snapshot(self):
        groups: list[ToolGroup] = []
        task = Task(
            1,
            "Empty",
            "session-1",
            TaskType.ReAct,
            "No tools",
            tool_groups=groups,
        )

        groups.append(ToolGroup("later", [], "Added later"))

        self.assertEqual(task.tool_groups, ())

    def test_failed_tool_group_update_preserves_previous_authorization(self):
        read = Tool(
            "read",
            "Read a file",
            "localhost:6410",
            input_schema=EMPTY_INPUT_SCHEMA,
        )
        task = Task(
            1,
            "Read",
            "session-1",
            TaskType.ReAct,
            "Read a file",
            tool_groups=[ToolGroup("files", [read], "File tools")],
        )
        old_groups = task.tool_groups
        old_resolver = task.tool_call_resolver
        duplicate_groups = [
            ToolGroup("first", [read], "First group"),
            ToolGroup(
                "second",
                [
                    Tool(
                        "read",
                        "Another read tool",
                        "localhost:6411",
                        input_schema=EMPTY_INPUT_SCHEMA,
                    )
                ],
                "Second group",
            ),
        ]

        with self.assertRaises(DuplicateToolNameError):
            task.update_tool_groups(duplicate_groups)

        self.assertIs(task.tool_groups, old_groups)
        self.assertIs(task.tool_call_resolver, old_resolver)
        self.assertIs(
            task.tool_call_resolver.resolve(
                [ToolCall("read", {}, "call_1")]
            )[0].tool,
            read,
        )

    def test_constructor_rejects_duplicate_tool_names(self):
        groups = [
            ToolGroup(
                "local-search",
                [
                    Tool(
                        "search",
                        "Local search",
                        "localhost:6410",
                        input_schema=EMPTY_INPUT_SCHEMA,
                    )
                ],
                "Local tools",
            ),
            ToolGroup(
                "remote-search",
                [
                    Tool(
                        "search",
                        "Remote search",
                        "localhost:6411",
                        input_schema=EMPTY_INPUT_SCHEMA,
                    )
                ],
                "Remote tools",
            ),
        ]

        with self.assertRaisesRegex(DuplicateToolNameError, "search"):
            Task(
                1,
                "Search",
                "session-1",
                TaskType.ReAct,
                "Search",
                tool_groups=groups,
            )


if __name__ == "__main__":
    unittest.main()
