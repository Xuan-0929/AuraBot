import sqlite3
import warnings
from contextlib import closing

from .utils import *
from ...skills.SkillGroup import SkillGroup



"""
用于存放SkillGroup系统与SQLite交互的工具函数

这一层直接与SQL交互。
"""



class SkillGroupSQL:
    """
    这个类没有必要被实例化，只是一个工具包
    """

    @staticmethod
    def _skill_group_from_row(row: sqlite3.Row,
                              include_skills: bool = True) -> SkillGroup:
        skills = []
        if include_skills:
            from .SkillSQL import SkillSQL

            skills, _ = SkillSQL.list_skills(row["name"])
        return SkillGroup(row["name"], skills, description=row["description"])

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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
            try:
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
                connection.commit()
                return int(cursor.lastrowid)
            except sqlite3.IntegrityError:
                connection.rollback()
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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
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
            connection.commit()
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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
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

            try:
                cursor = connection.execute(
                    f"UPDATE skill_group SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                connection.commit()
                return cursor.rowcount > 0
            except sqlite3.IntegrityError:
                connection.rollback()
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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
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
            return SkillGroupSQL._skill_group_from_row(row, include_skills)

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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
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
            return SkillGroupSQL._skill_group_from_row(row, include_skills)

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
        with closing(get_connection()) as connection:
            ensure_schema(connection)
            rows = connection.execute(
                """
                SELECT id, name, description
                FROM skill_group
                ORDER BY id
                """
            ).fetchall()
            return (
                [
                    SkillGroupSQL._skill_group_from_row(row, include_skills)
                    for row in rows
                ],
                [int(row["id"]) for row in rows],
            )
