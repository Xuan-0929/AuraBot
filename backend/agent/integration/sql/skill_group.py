"""
负责 SkillGroup 聚合及其成员关系的 SQL 持久化。

manager 通过本 adapter 在 transaction 边界内加载或写入 group 与关联 Skill 记录，
再还原领域聚合；本模块不缓存共享实例，也不参与 Task 能力租约。
"""

import sqlite3
import warnings


from ...skills.skill import Skill
from ...skills.skill_group import SkillGroup
from .utils import read_connection, transaction



"""
用于存放SkillGroup系统与SQLite交互的工具函数

这一层直接与SQL交互。
"""



class SkillGroupSQL:
    """
    这个类没有必要被实例化，只是一个工具包
    """

    @staticmethod
    def _skill_groups_from_joined_rows(
        rows: list[sqlite3.Row],
    ) -> tuple[list[SkillGroup], list[int]]:
        grouped: dict[int, tuple[str, str | None, list[Skill]]] = {}

        for row in rows:
            group_id = int(row["group_id"])
            if group_id not in grouped:
                grouped[group_id] = (
                    row["group_name"],
                    row["group_description"],
                    [],
                )

            if row["skill_id"] is not None:
                grouped[group_id][2].append(
                    Skill(row["skill_name"], path=row["skill_url"])
                )

        return (
            [
                SkillGroup(name, skills, description=description)
                for name, description, skills in grouped.values()
            ],
            list(grouped),
        )

    @staticmethod
    def _skill_group_without_skills(row: sqlite3.Row) -> SkillGroup:
        return SkillGroup(row["name"], [], description=row["description"])

    @staticmethod
    def insert_skill_group_metadata(name: str, description: str | None = None,
                                    is_builtin: bool = False) -> int | None:
        """
        写入一条SkillGroup的元数据

        arg:
            name: SkillGroup名称
            description: SkillGroup描述
            is_builtin: SkillGroup是否项目原生

        ret:
            int: 新创建的SkillGroup ID
            None: 写入失败，例如名称重复
        """
        try:
            with transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO skill_group (
                        name,
                        description,
                        is_builtin
                    )
                    VALUES (?, ?, ?)
                    """,
                    (name, description, int(is_builtin)),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def delete_skill_group_metadata(name: str) -> bool:
        """
        删除一条SkillGroup的记录

        项目原生SkillGroup无法被删除，弹出一个警告

        arg:
            name: SkillGroup名称

        ret:
            True: 删除成功
            False: 删除失败，例如不存在，或是项目原生SkillGroup
        """
        with transaction() as connection:
            row = connection.execute(
                "SELECT id, is_builtin FROM skill_group WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                return False
            if bool(row["is_builtin"]):
                warnings.warn("项目原生SkillGroup无法被删除", RuntimeWarning)
                return False

            cursor = connection.execute(
                "DELETE FROM skill_group WHERE id = ?",
                (row["id"],),
            )
            return cursor.rowcount > 0

    @staticmethod
    def update_skill_group_metadata(name: str, *,
                                    new_name: str | None = None,
                                    description: str | None = None,
                                    is_builtin: bool | None = None) -> bool:
        """
        对于给定SkillGroup，更新元信息

        如果元信息为None，则不更新该字段

        arg:
            name: 受到修改的SkillGroup名称
            new_name: 新的SkillGroup名称
            description: 新的SkillGroup描述
            is_builtin: 新的is_builtin

        ret:
            True: 更新成功
            False: 更新失败，例如目标不存在，或new_name冲突
        """
        try:
            with transaction() as connection:
                row = connection.execute(
                    "SELECT id FROM skill_group WHERE name = ?",
                    (name,),
                ).fetchone()
                if row is None:
                    return False

                updates: list[str] = []
                values: list[object] = []

                if new_name is not None:
                    updates.append("name = ?")
                    values.append(new_name)
                if description is not None:
                    updates.append("description = ?")
                    values.append(description)
                if is_builtin is not None:
                    updates.append("is_builtin = ?")
                    values.append(int(is_builtin))

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])

                cursor = connection.execute(
                    f"UPDATE skill_group SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_skill_group(name: str, *,
                        include_skills: bool = True) -> SkillGroup | None:
        """
        获取一个运行时SkillGroup对象

        arg:
            name: SkillGroup名称
            include_skills: 是否同时加载该SkillGroup下的Skill对象

        ret:
            SkillGroup: 运行时SkillGroup对象
            None: 不存在
        """
        with read_connection() as connection:
            if not include_skills:
                row = connection.execute(
                    """
                    SELECT id, name, description
                    FROM skill_group
                    WHERE name = ?
                    """,
                    (name,),
                ).fetchone()
                if row is None:
                    return None
                return SkillGroupSQL._skill_group_without_skills(row)

            rows = connection.execute(
                """
                SELECT
                    skill_group.id AS group_id,
                    skill_group.name AS group_name,
                    skill_group.description AS group_description,
                    skill.id AS skill_id,
                    skill.name AS skill_name,
                    skill.url AS skill_url
                FROM skill_group
                LEFT JOIN skill ON skill.group_id = skill_group.id
                WHERE skill_group.name = ?
                ORDER BY skill.id
                """,
                (name,),
            ).fetchall()
            groups, _ = SkillGroupSQL._skill_groups_from_joined_rows(rows)
            return groups[0] if groups else None

    @staticmethod
    def get_skill_group_by_id(group_id: int, *,
                              include_skills: bool = True) -> SkillGroup | None:
        """
        通过数据库ID获取一个运行时SkillGroup对象

        arg:
            group_id: SkillGroup数据库ID
            include_skills: 是否同时加载该SkillGroup下的Skill对象

        ret:
            SkillGroup: 运行时SkillGroup对象
            None: 不存在
        """
        with read_connection() as connection:
            if not include_skills:
                row = connection.execute(
                    """
                    SELECT id, name, description
                    FROM skill_group
                    WHERE id = ?
                    """,
                    (group_id,),
                ).fetchone()
                if row is None:
                    return None
                return SkillGroupSQL._skill_group_without_skills(row)

            rows = connection.execute(
                """
                SELECT
                    skill_group.id AS group_id,
                    skill_group.name AS group_name,
                    skill_group.description AS group_description,
                    skill.id AS skill_id,
                    skill.name AS skill_name,
                    skill.url AS skill_url
                FROM skill_group
                LEFT JOIN skill ON skill.group_id = skill_group.id
                WHERE skill_group.id = ?
                ORDER BY skill.id
                """,
                (group_id,),
            ).fetchall()
            groups, _ = SkillGroupSQL._skill_groups_from_joined_rows(rows)
            return groups[0] if groups else None

    @staticmethod
    def list_skill_groups(*,
                          include_skills: bool = True) -> tuple[list[SkillGroup], list[int]]:
        """
        获取所有运行时SkillGroup对象

        arg:
            include_skills: 是否同时加载每个SkillGroup下的Skill对象

        ret:
            tuple[list[SkillGroup], list[int]]: 所有SkillGroup对象及其在数据库的ID
        """
        with read_connection() as connection:
            if not include_skills:
                rows = connection.execute(
                    """
                    SELECT id, name, description
                    FROM skill_group
                    ORDER BY id
                    """
                ).fetchall()
                return (
                    [SkillGroupSQL._skill_group_without_skills(row) for row in rows],
                    [int(row["id"]) for row in rows],
                )

            rows = connection.execute(
                """
                SELECT
                    skill_group.id AS group_id,
                    skill_group.name AS group_name,
                    skill_group.description AS group_description,
                    skill.id AS skill_id,
                    skill.name AS skill_name,
                    skill.url AS skill_url
                FROM skill_group
                LEFT JOIN skill ON skill.group_id = skill_group.id
                ORDER BY skill_group.id, skill.id
                """
            ).fetchall()
            return SkillGroupSQL._skill_groups_from_joined_rows(rows)
