import unittest

from backend.agent.skills.SkillGroup import SkillGroup
from backend.agent.task.Task import Task, TaskType
from backend.agent.tools.Tool import Tool
from backend.agent.tools.ToolGroup import ToolGroup
from backend.agent.tools.core.ToolCallEntity import ToolCall
from backend.agent.tools.core.ToolExceptions import (
    DuplicateToolNameError,
    ToolNotAuthorizedError,
)


EMPTY_INPUT_SCHEMA = {"type": "object", "properties": {}}


class TaskTypeTests(unittest.TestCase):
    def test_task_type_defines_supported_agent_modes(self):
        self.assertEqual(TaskType.ReAct.value, "ReAct")
        self.assertEqual(TaskType.PlanAndSolve.value, "PlanAndSolve")


class TaskTests(unittest.TestCase):
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

        self.assertEqual(task.skill_groups, skill_groups)
        self.assertEqual(task.tool_groups, tuple(tool_groups))
        self.assertIsNotNone(task.tool_call_resolver)

    def test_constructor_creates_independent_group_state(self):
        first = Task(1, "First", "session-1", TaskType.ReAct, "First task")
        second = Task(2, "Second", "session-1", TaskType.ReAct, "Second task")

        first.skill_groups.append(SkillGroup("research", []))
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

        self.assertEqual(second.skill_groups, [])
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
            task.tool_call_resolver.resolve([ToolCall("missing", {})])

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
        resolved = task.tool_call_resolver.resolve_message(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "read", "arguments": "{}"},
                    }
                ],
            }
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
            task.tool_call_resolver.resolve([ToolCall("read", {})])
        self.assertIs(
            task.tool_call_resolver.resolve([ToolCall("write", {})])[0].tool,
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
            task.tool_call_resolver.resolve([ToolCall("read", {})])[0].tool,
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
