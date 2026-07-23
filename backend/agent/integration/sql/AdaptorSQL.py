import sqlite3
from typing import TypedDict

from .utils import read_connection, transaction


class AdaptorMetadata(TypedDict):
    name: str
    description: str | None


class AdaptorSQL:
    """无需实例化的 Adaptor SQLite 工具类。"""

    @staticmethod
    def _is_valid_name(name: str) -> bool:
        return isinstance(name, str) and bool(name) and name == name.strip()

    @staticmethod
    def _adaptor_from_row(row: sqlite3.Row) -> AdaptorMetadata:
        return {
            "name": row["name"],
            "description": row["description"],
        }

    @staticmethod
    def insert_adaptor_metadata(
        name: str,
        description: str | None = None,
    ) -> int | None:
        """写入一条 Adaptor 元数据；名称无效或重复时返回 None。"""

        if not AdaptorSQL._is_valid_name(name):
            return None

        try:
            with transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO adaptor (name, description)
                    VALUES (?, ?)
                    """,
                    (name, description),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def get_adaptor(name: str) -> AdaptorMetadata | None:
        """按全局唯一名称获取 Adaptor 元数据。"""

        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT name, description
                FROM adaptor
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            if row is None:
                return None
            return AdaptorSQL._adaptor_from_row(row)

    @staticmethod
    def get_adaptor_by_id(
        adaptor_id: int,
    ) -> AdaptorMetadata | None:
        """按数据库 ID 获取 Adaptor 元数据。"""

        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT name, description
                FROM adaptor
                WHERE id = ?
                """,
                (adaptor_id,),
            ).fetchone()
            if row is None:
                return None
            return AdaptorSQL._adaptor_from_row(row)

    @staticmethod
    def list_adaptors() -> tuple[list[AdaptorMetadata], list[int]]:
        """按 ID 顺序获取全部 Adaptor 元数据及数据库 ID。"""

        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, description
                FROM adaptor
                ORDER BY id
                """
            ).fetchall()
            return (
                [AdaptorSQL._adaptor_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )

    @staticmethod
    def update_adaptor_metadata(
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
    ) -> bool:
        """更新 Adaptor 元数据；None 表示不更新对应字段。"""

        if new_name is not None and not AdaptorSQL._is_valid_name(new_name):
            return False

        try:
            with transaction() as connection:
                row = connection.execute(
                    "SELECT id FROM adaptor WHERE name = ?",
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

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])
                cursor = connection.execute(
                    f"UPDATE adaptor SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def delete_adaptor_metadata(name: str) -> bool:
        """删除未被 Tool 引用的 Adaptor。"""

        try:
            with transaction() as connection:
                row = connection.execute(
                    """
                    SELECT
                        adaptor.id,
                        EXISTS (
                            SELECT 1
                            FROM tool
                            WHERE tool.adaptor_id = adaptor.id
                        ) AS has_tools
                    FROM adaptor
                    WHERE adaptor.name = ?
                    """,
                    (name,),
                ).fetchone()
                if row is None or bool(row["has_tools"]):
                    return False

                cursor = connection.execute(
                    "DELETE FROM adaptor WHERE id = ?",
                    (row["id"],),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False


__all__ = ["AdaptorMetadata", "AdaptorSQL"]
