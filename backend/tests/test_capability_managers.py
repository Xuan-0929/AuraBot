import unittest

from backend.agent.capabilities.lease import CapabilitySession
from backend.agent.skills.skill import Skill
from backend.agent.skills.skill_group import SkillGroup
from backend.agent.skills.manager import SkillGroupManager
from backend.agent.tools.tool import Tool
from backend.agent.tools.tool_group import ToolGroup
from backend.agent.tools.manager import ToolGroupManager


def tool_group(name: str) -> ToolGroup:
    return ToolGroup(
        name,
        [
            Tool(
                f"{name}_tool",
                f"{name} tool",
                "http://127.0.0.1:6410/mcp",
                input_schema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            )
        ],
        f"{name} tools",
    )


def skill_group(name: str) -> SkillGroup:
    return SkillGroup(name, [Skill(f"{name}-skill")], f"{name} skills")


class CapabilityManagerTests(unittest.TestCase):
    def test_tool_groups_are_lazy_shared_and_destroyed_at_zero_references(self):
        loads: list[str] = []

        def load(name: str):
            loads.append(name)
            return tool_group(name)

        manager = ToolGroupManager(loader=load)

        first = manager.acquire(("files",))
        second = manager.acquire(("files",))

        self.assertIs(first[0], second[0])
        self.assertEqual(loads, ["files"])
        self.assertEqual(manager.reference_count("files"), 2)

        manager.release(first)
        self.assertEqual(manager.reference_count("files"), 1)
        manager.release(second)
        self.assertEqual(manager.reference_count("files"), 0)
        self.assertIsNone(manager.get_cached("files"))

    def test_native_factory_and_database_loader_use_one_lifecycle(self):
        native = tool_group("Plan")
        manager = ToolGroupManager(
            loader=lambda name: None,
            native_factories={"Plan": lambda: native},
        )

        acquired = manager.acquire(("Plan",))

        self.assertEqual(acquired, (native,))
        self.assertEqual(manager.reference_count("Plan"), 1)
        manager.release(acquired)
        self.assertIsNone(manager.get_cached("Plan"))

    def test_failed_batch_acquisition_rolls_back_every_reference(self):
        manager = ToolGroupManager(
            loader=lambda name: tool_group(name) if name == "files" else None
        )

        with self.assertRaisesRegex(LookupError, "missing"):
            manager.acquire(("files", "missing"))

        self.assertEqual(manager.reference_count("files"), 0)
        self.assertIsNone(manager.get_cached("files"))

    def test_skill_groups_follow_the_same_reference_count_contract(self):
        manager = SkillGroupManager(loader=skill_group)

        group = manager.acquire(("research",))

        self.assertEqual(group[0].name, "research")
        self.assertEqual(manager.reference_count("research"), 1)
        manager.release(group)
        self.assertIsNone(manager.get_cached("research"))

    def test_capability_session_acquires_each_name_once_and_closes_idempotently(self):
        tool_manager = ToolGroupManager(loader=tool_group)
        skill_manager = SkillGroupManager(loader=skill_group)
        session = CapabilitySession(tool_manager, skill_manager)

        first = session.acquire_tool_groups(("files", "files"))
        second = session.acquire_tool_groups(("files",))
        skills = session.acquire_skill_groups(("research",))

        self.assertEqual(first, second)
        self.assertEqual(tool_manager.reference_count("files"), 1)
        self.assertEqual(skill_manager.reference_count("research"), 1)

        session.close()
        session.close()

        self.assertEqual(tool_manager.reference_count("files"), 0)
        self.assertEqual(skill_manager.reference_count("research"), 0)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            session.acquire_tool_groups(("shell",))

    def test_closed_managers_reject_acquire_but_allow_outstanding_release(self):
        tool_manager = ToolGroupManager(loader=tool_group)
        skill_manager = SkillGroupManager(loader=skill_group)
        tools = tool_manager.acquire(("files",))
        skills = skill_manager.acquire(("research",))

        tool_manager.close()
        skill_manager.close()

        with self.assertRaisesRegex(RuntimeError, "closed"):
            tool_manager.acquire(("shell",))
        with self.assertRaisesRegex(RuntimeError, "closed"):
            skill_manager.acquire(("writing",))
        tool_manager.release(tools)
        skill_manager.release(skills)
        self.assertEqual(tool_manager.reference_count("files"), 0)
        self.assertEqual(skill_manager.reference_count("research"), 0)

    def test_release_validates_values_before_reading_names(self):
        tool_manager = ToolGroupManager(loader=tool_group)
        skill_manager = SkillGroupManager(loader=skill_group)

        with self.assertRaises(TypeError):
            tool_manager.release((object(),))
        with self.assertRaises(TypeError):
            skill_manager.release((object(),))


if __name__ == "__main__":
    unittest.main()
