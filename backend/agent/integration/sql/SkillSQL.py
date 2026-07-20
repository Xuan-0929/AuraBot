import sqlite3
import warnings

from ...skills.Skill import Skill
from .utils import read_connection, transaction


"""
用于存放Skill系统与SQLite交互的工具函数

从软件架构上来说，这一层是直接与SQLite交互的
"""



class SkillSQL:
    """
    这个类没有必要被实例化，只是一个工具包
    """

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> Skill:
        return Skill(
            row["name"],
            path=row["url"])

    @staticmethod
    def _get_group_id(connection: sqlite3.Connection, group_name: str) -> int | None:
        row = connection.execute(
            "SELECT id FROM skill_group WHERE name = ?",
            (group_name,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    @staticmethod
    def insert_skill_metadata(name: str, path: str,
                              is_builtin: bool, is_global_url: bool,
                              group_name: str) -> int | None:
        """
        写入一条Skill的元数据

        arg:
            name: Skill名称
            path: Skill在本机的路径，或者基于项目根目录的路径
            is_builtin: Skill是否项目原生
            is_global_url: Skill是否基于全局路径
            group_name: 所属SkillGroup名称
        
        ret:
            int: 新创建的Skill ID
            None: 写入失败，例如SkillGroup不存在，或同组内Skill名称重复
        """
        try:
            with transaction() as connection:
                group_id = SkillSQL._get_group_id(connection, group_name)
                if group_id is None:
                    return None

                cursor = connection.execute(
                    """
                    INSERT INTO skill (
                        group_id,
                        name,
                        url,
                        is_builtin,
                        is_global_url
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        group_id,
                        name,
                        path,
                        int(is_builtin),
                        int(is_global_url),
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None


    @staticmethod
    def delete_skill_metadata(group_name: str, name: str) -> bool:
        """
        删除一条Skill的记录

        项目原生Skill无法被删除，弹出一个警告

        arg:
            group_name: 所属SkillGroup名称
            name: Skill名称
        
        ret:
            True: 删除成功
            False: 删除失败，例如不存在，或是项目原生Skill
        """
        with transaction() as connection:
            row = connection.execute(
                """
                SELECT skill.id, skill.is_builtin
                FROM skill
                JOIN skill_group ON skill_group.id = skill.group_id
                WHERE skill_group.name = ?
                  AND skill.name = ?
                """,
                (group_name, name),
            ).fetchone()
            if row is None:
                return False
            if bool(row["is_builtin"]):
                warnings.warn("项目原生Skill无法被删除", RuntimeWarning)
                return False

            cursor = connection.execute(
                "DELETE FROM skill WHERE id = ?",
                (row["id"],),
            )
            return cursor.rowcount > 0


    @staticmethod
    def update_skill_metadata(group_name: str, name: str, *,
                              new_name: str | None = None, path: str | None = None,
                              is_builtin: bool | None = None,
                              is_global_url: bool | None = None,
                              new_group_name: str | None = None) -> bool:
        """
        对于给定Skill，更新元信息

        使用 group_name + name 定位旧记录。
        如果元信息为None，则不更新该字段

        arg:
            group_name: 当前所属SkillGroup名称
            name: 当前Skill名称
            new_name: 新的Skill名称
            path: 新的Skill路径
            is_builtin: 新的is_builtin
            is_global_url: 新的is_global_url
            new_group_name: 新的所属SkillGroup名称
        
            
        ret:
            True: 更新成功
            False: 更新失败，例如目标不存在、目标SkillGroup不存在、同组内名称冲突
        """
        try:
            with transaction() as connection:
                row = connection.execute(
                    """
                    SELECT skill.id
                    FROM skill
                    JOIN skill_group ON skill_group.id = skill.group_id
                    WHERE skill_group.name = ?
                      AND skill.name = ?
                    """,
                    (group_name, name),
                ).fetchone()
                if row is None:
                    return False

                updates: list[str] = []
                values: list[object] = []

                if new_name is not None:
                    updates.append("name = ?")
                    values.append(new_name)
                if path is not None:
                    updates.append("url = ?")
                    values.append(path)
                if is_builtin is not None:
                    updates.append("is_builtin = ?")
                    values.append(int(is_builtin))
                if is_global_url is not None:
                    updates.append("is_global_url = ?")
                    values.append(int(is_global_url))
                if new_group_name is not None:
                    new_group_id = SkillSQL._get_group_id(connection, new_group_name)
                    if new_group_id is None:
                        return False
                    updates.append("group_id = ?")
                    values.append(new_group_id)

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])

                cursor = connection.execute(
                    f"UPDATE skill SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_skill(group_name: str, name: str) -> Skill | None:
        """
        获取一个运行时Skill对象

        Skill名称允许跨SkillGroup重复，因此必须使用 group_name + name 定位。

        arg:
            group_name: 所属SkillGroup名称
            name: Skill名称
        
        ret:
            Skill: 运行时Skill对象，只包含name和path
            None: 不存在
        """
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT skill.name, skill.url
                FROM skill
                JOIN skill_group ON skill_group.id = skill.group_id
                WHERE skill_group.name = ?
                  AND skill.name = ?
                """,
                (group_name, name),
            ).fetchone()
            if row is None:
                return None
            return SkillSQL._skill_from_row(row)


    @staticmethod
    def get_skill_by_id(skill_id: int) -> Skill | None:
        """
        通过数据库ID获取一个运行时Skill对象

        arg:
            skill_id: Skill数据库ID

        ret:
            Skill: 运行时Skill对象，只包含name和path
            None: 不存在
        """
        with read_connection() as connection:
            row = connection.execute(
                "SELECT name, url FROM skill WHERE id = ?",
                (skill_id,),
            ).fetchone()
            if row is None:
                return None
            return SkillSQL._skill_from_row(row)


    @staticmethod
    def list_skills(group_name: str) -> tuple[list[Skill], list[int]]:
        """
        获取某个SkillGroup下的所有运行时Skill对象

        arg:
            group_name: SkillGroup名称

        ret:
            tuple[list[Skill], list[int]]: 该SkillGroup下的所有Skill对象及其在数据库的ID
        """
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT skill.id, skill.name, skill.url
                FROM skill
                JOIN skill_group ON skill_group.id = skill.group_id
                WHERE skill_group.name = ?
                ORDER BY skill.id
                """,
                (group_name,),
            ).fetchall()
            return (
                [SkillSQL._skill_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )


    @staticmethod
    def list_skills_by_name(name: str) -> tuple[list[Skill], list[int]]:
        """
        获取所有同名Skill对象

        因为Skill名称允许跨SkillGroup重复，所以这里可能返回多个Skill。
        返回对象本体只包含name和path，不包含所属SkillGroup信息。

        arg:
            name: Skill名称

        ret:
            tuple[list[Skill], list[int]]: 所有同名Skill对象及其在数据库的ID
        """
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, url
                FROM skill
                WHERE name = ?
                ORDER BY id
                """,
                (name,),
            ).fetchall()
            return (
                [SkillSQL._skill_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )
