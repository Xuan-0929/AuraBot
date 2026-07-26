"""
验证 agent SQL connection、transaction 与 schema 初始化辅助函数。

测试以临时 SQLite 数据库观察真实提交/回滚及 adapter 交互，不覆盖领域 manager 缓存。
"""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.agent.integration.sql import utils
from backend.agent.integration.sql.skill_group import SkillGroupSQL
from backend.agent.integration.sql.skill import SkillSQL
from backend.agent.skills.skill import Skill
from backend.agent.skills.skill_group import SkillGroup


class AgentSQLUtilsTests(unittest.TestCase):
    def test_runtime_sqlite_path_lives_next_to_utils_module(self):
        self.assertEqual(utils.SQLITE_PATH.name, "agent_runtime_sql.sqlite")
        self.assertEqual(utils.SQLITE_PATH.parent, Path(utils.__file__).parent)

    def test_create_connection_configures_runtime_sqlite(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                connection = utils.create_connection()

                try:
                    self.assertIsInstance(connection, sqlite3.Connection)
                    self.assertTrue(sqlite_path.exists())
                    self.assertIs(connection.row_factory, sqlite3.Row)
                    self.assertEqual(
                        connection.execute("PRAGMA foreign_keys").fetchone()[0],
                        1,
                    )

                    connection.execute("create table runtime_context (value text)")
                    connection.execute(
                        "insert into runtime_context (value) values (?)",
                        ("hello",),
                    )
                    row = connection.execute(
                        "select value from runtime_context"
                    ).fetchone()

                    self.assertEqual(row["value"], "hello")
                finally:
                    connection.close()

    def test_connection_contexts_close_and_manage_transactions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                utils.initialize_schema()

                with utils.transaction() as connection:
                    connection.execute(
                        "INSERT INTO mcp_server (name, url) VALUES (?, ?)",
                        ("committed", "localhost:6410"),
                    )

                with self.assertRaises(RuntimeError):
                    with utils.transaction() as connection:
                        connection.execute(
                            "INSERT INTO mcp_server (name, url) VALUES (?, ?)",
                            ("rolled-back", "localhost:6411"),
                        )
                        raise RuntimeError("force rollback")

                with utils.read_connection() as connection:
                    rows = connection.execute(
                        "SELECT name FROM mcp_server ORDER BY id"
                    ).fetchall()

                self.assertEqual([row["name"] for row in rows], ["committed"])
                with self.assertRaises(sqlite3.ProgrammingError):
                    connection.execute("SELECT 1")

    def test_skill_group_crud_returns_runtime_objects(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                utils.initialize_schema()
                group_id = SkillGroupSQL.insert_skill_group_metadata(
                    "core",
                    "Core skills",
                    is_builtin=False,
                )

                self.assertIsInstance(group_id, int)
                self.assertIsNone(
                    SkillGroupSQL.insert_skill_group_metadata("core")
                )

                group = SkillGroupSQL.get_skill_group(
                    "core",
                    include_skills=False,
                )
                self.assertIsInstance(group, SkillGroup)
                self.assertEqual(group.name, "core")
                self.assertEqual(group.description, "Core skills")
                self.assertEqual(group.skills, [])

                self.assertTrue(
                    SkillGroupSQL.update_skill_group_metadata(
                        "core",
                        new_name="system",
                        description="System skills",
                    )
                )
                self.assertIsNone(SkillGroupSQL.get_skill_group("core"))
                self.assertEqual(
                    SkillGroupSQL.get_skill_group("system", include_skills=False).description,
                    "System skills",
                )

                groups, ids = SkillGroupSQL.list_skill_groups(include_skills=False)
                self.assertEqual([group.name for group in groups], ["system"])
                self.assertEqual(ids, [group_id])

                self.assertTrue(SkillGroupSQL.delete_skill_group_metadata("system"))
                self.assertIsNone(SkillGroupSQL.get_skill_group("system"))

    def test_skill_crud_allows_same_name_in_different_groups(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                utils.initialize_schema()
                SkillGroupSQL.insert_skill_group_metadata("alpha")
                SkillGroupSQL.insert_skill_group_metadata("beta")

                alpha_id = SkillSQL.insert_skill_metadata(
                    "search",
                    "alpha/search/SKILL.md",
                    is_builtin=False,
                    is_global_url=False,
                    group_name="alpha",
                )
                beta_id = SkillSQL.insert_skill_metadata(
                    "search",
                    "beta/search/SKILL.md",
                    is_builtin=False,
                    is_global_url=False,
                    group_name="beta",
                )

                self.assertIsInstance(alpha_id, int)
                self.assertIsInstance(beta_id, int)
                self.assertNotEqual(alpha_id, beta_id)
                self.assertIsNone(
                    SkillSQL.insert_skill_metadata(
                        "search",
                        "alpha/duplicate/SKILL.md",
                        is_builtin=False,
                        is_global_url=False,
                        group_name="alpha",
                    )
                )

                alpha_skill = SkillSQL.get_skill("alpha", "search")
                beta_skill = SkillSQL.get_skill_by_id(beta_id)
                self.assertIsInstance(alpha_skill, Skill)
                self.assertEqual(alpha_skill.name, "search")
                self.assertEqual(str(alpha_skill.path), "alpha/search/SKILL.md")
                self.assertEqual(str(beta_skill.path), "beta/search/SKILL.md")

                skills, ids = SkillSQL.list_skills_by_name("search")
                self.assertEqual([str(skill.path) for skill in skills], [
                    "alpha/search/SKILL.md",
                    "beta/search/SKILL.md",
                ])
                self.assertEqual(ids, [alpha_id, beta_id])

                self.assertTrue(
                    SkillSQL.update_skill_metadata(
                        "alpha",
                        "search",
                        new_name="lookup",
                        path="alpha/lookup/SKILL.md",
                    )
                )
                self.assertIsNone(SkillSQL.get_skill("alpha", "search"))
                self.assertEqual(
                    str(SkillSQL.get_skill("alpha", "lookup").path),
                    "alpha/lookup/SKILL.md",
                )

                skills, ids = SkillSQL.list_skills("alpha")
                self.assertEqual([skill.name for skill in skills], ["lookup"])
                self.assertEqual(ids, [alpha_id])

                with patch.object(
                    utils,
                    "create_connection",
                    wraps=utils.create_connection,
                ) as create_connection:
                    group = SkillGroupSQL.get_skill_group("alpha")

                self.assertEqual(create_connection.call_count, 1)
                self.assertEqual([skill.name for skill in group.skills], ["lookup"])

                with patch.object(
                    utils,
                    "create_connection",
                    wraps=utils.create_connection,
                ) as create_connection:
                    groups, _ = SkillGroupSQL.list_skill_groups()

                self.assertEqual(create_connection.call_count, 1)
                self.assertEqual([group.name for group in groups], ["alpha", "beta"])
                self.assertEqual(
                    [[skill.name for skill in group.skills] for group in groups],
                    [["lookup"], ["search"]],
                )

                self.assertTrue(SkillSQL.delete_skill_metadata("alpha", "lookup"))
                self.assertIsNone(SkillSQL.get_skill("alpha", "lookup"))

    def test_builtin_records_are_not_deleted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sqlite_path = Path(tmpdir) / "agent_runtime_sql.sqlite"

            with patch.object(utils, "SQLITE_PATH", sqlite_path):
                utils.initialize_schema()
                SkillGroupSQL.insert_skill_group_metadata("builtin-group", is_builtin=True)
                SkillSQL.insert_skill_metadata(
                    "builtin-skill",
                    "builtin/SKILL.md",
                    is_builtin=True,
                    is_global_url=False,
                    group_name="builtin-group",
                )

                with self.assertWarns(RuntimeWarning):
                    self.assertFalse(
                        SkillSQL.delete_skill_metadata(
                            "builtin-group",
                            "builtin-skill",
                        )
                    )
                self.assertIsNotNone(SkillSQL.get_skill("builtin-group", "builtin-skill"))
                with self.assertWarns(RuntimeWarning):
                    self.assertFalse(
                        SkillGroupSQL.delete_skill_group_metadata("builtin-group")
                    )
                self.assertIsNotNone(SkillGroupSQL.get_skill_group("builtin-group"))


if __name__ == "__main__":
    unittest.main()
