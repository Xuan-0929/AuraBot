"""
负责 ToolGroup 聚合和成员关系的 SQL CRUD 与领域映射。

ToolGroupManager 通过本 adapter 在显式 transaction 中加载、保存或删除 group 及 Tool
关联；本模块不维护运行时缓存、不解析 tool_call，也不执行远端工具。
"""

import sqlite3
import warnings


from ...tools.tool import Tool
from ...tools.tool_group import ToolGroup
from ...tools.utils.formatter import (
    parse_optional_schema_json,
    parse_schema_json,
)
from .utils import read_connection, transaction


"""
用于存放ToolGroup系统与SQLite交互的工具函数。

这一层直接与SQLite交互。
"""


class ToolGroupSQL:
    """无需实例化的ToolGroup SQLite工具类。"""

    @staticmethod
    def _tool_groups_from_joined_rows(
        rows: list[sqlite3.Row],
    ) -> tuple[list[ToolGroup], list[int]]:
        grouped: dict[int, tuple[str, str, list[Tool]]] = {}

        for row in rows:
            group_id = int(row["group_id"])
            if group_id not in grouped:
                grouped[group_id] = (
                    row["group_name"],
                    row["group_description"],
                    [],
                )

            if row["tool_id"] is not None:
                adaptor_name = row["tool_adaptor_name"]
                grouped[group_id][2].append(
                    Tool(
                        row["tool_name"],
                        description=row["tool_description"],
                        server_url=row["server_url"],
                        input_schema=parse_schema_json(
                            row["tool_input_schema_json"]
                        ),
                        output_schema=parse_optional_schema_json(
                            row["tool_output_schema_json"]
                        ),
                        adaptor_names=(
                            (adaptor_name,)
                            if adaptor_name is not None
                            else ()
                        ),
                    )
                )

        return (
            [
                ToolGroup(name, tools, description=description)
                for name, description, tools in grouped.values()
            ],
            list(grouped),
        )

    @staticmethod
    def _tool_group_without_tools(row: sqlite3.Row) -> ToolGroup:
        return ToolGroup(row["name"], [], description=row["description"])

    @staticmethod
    def _get_server_id(
        connection: sqlite3.Connection,
        server_url: str,
    ) -> int | None:
        row = connection.execute(
            "SELECT id FROM mcp_server WHERE url = ?",
            (server_url,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"])

    @staticmethod
    def insert_tool_group_metadata(
        name: str,
        server_url: str,
        description: str,
        is_native: bool = False,
    ) -> int | None:
        """
        写入一条ToolGroup元数据。

        ret:
            int: 新创建的ToolGroup ID
            None: MCP Server不存在、名称重复或约束校验失败
        """
        if not name or not server_url or not description:
            return None

        try:
            with transaction() as connection:
                server_id = ToolGroupSQL._get_server_id(connection, server_url)
                if server_id is None:
                    return None

                cursor = connection.execute(
                    """
                    INSERT INTO tool_group (
                        server_id,
                        name,
                        description,
                        is_native
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (server_id, name, description, int(is_native)),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def delete_tool_group_metadata(name: str) -> bool:
        """
        删除一条ToolGroup记录。

        原生或仍包含Tool的ToolGroup不能删除。
        """
        try:
            with transaction() as connection:
                row = connection.execute(
                    """
                    SELECT
                        tool_group.id,
                        tool_group.is_native,
                        EXISTS (
                            SELECT 1
                            FROM tool
                            WHERE tool.group_id = tool_group.id
                        ) AS has_tools
                    FROM tool_group
                    WHERE tool_group.name = ?
                    """,
                    (name,),
                ).fetchone()
                if row is None:
                    return False
                if bool(row["is_native"]):
                    warnings.warn("项目原生ToolGroup无法被删除", RuntimeWarning)
                    return False
                if bool(row["has_tools"]):
                    return False

                cursor = connection.execute(
                    "DELETE FROM tool_group WHERE id = ?",
                    (row["id"],),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def update_tool_group_metadata(
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
        is_native: bool | None = None,
    ) -> bool:
        """
        更新给定ToolGroup的元数据。

        None表示不更新对应字段。ToolGroup所属MCP Server不在此接口中修改。
        """
        if new_name is not None and not new_name:
            return False
        if description is not None and not description:
            return False

        try:
            with transaction() as connection:
                row = connection.execute(
                    "SELECT id FROM tool_group WHERE name = ?",
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
                if is_native is not None:
                    updates.append("is_native = ?")
                    values.append(int(is_native))

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])

                cursor = connection.execute(
                    f"UPDATE tool_group SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_tool_group(
        name: str,
        *,
        include_tools: bool = True,
    ) -> ToolGroup | None:
        """按全局唯一名称获取运行时ToolGroup对象。"""
        with read_connection() as connection:
            if not include_tools:
                row = connection.execute(
                    """
                    SELECT id, name, description
                    FROM tool_group
                    WHERE name = ?
                    """,
                    (name,),
                ).fetchone()
                if row is None:
                    return None
                return ToolGroupSQL._tool_group_without_tools(row)

            rows = connection.execute(
                """
                SELECT
                    tool_group.id AS group_id,
                    tool_group.name AS group_name,
                    tool_group.description AS group_description,
                    tool.id AS tool_id,
                    tool.name AS tool_name,
                    tool.description AS tool_description,
                    tool.input_schema_json AS tool_input_schema_json,
                    tool.output_schema_json AS tool_output_schema_json,
                    adaptor.name AS tool_adaptor_name,
                    mcp_server.url AS server_url
                FROM tool_group
                JOIN mcp_server ON mcp_server.id = tool_group.server_id
                LEFT JOIN tool
                    ON tool.group_id = tool_group.id
                   AND tool.server_id = tool_group.server_id
                LEFT JOIN adaptor ON adaptor.id = tool.adaptor_id
                WHERE tool_group.name = ?
                ORDER BY tool.id
                """,
                (name,),
            ).fetchall()
            groups, _ = ToolGroupSQL._tool_groups_from_joined_rows(rows)
            return groups[0] if groups else None

    @staticmethod
    def get_tool_group_by_id(
        group_id: int,
        *,
        include_tools: bool = True,
    ) -> ToolGroup | None:
        """按数据库ID获取运行时ToolGroup对象。"""
        with read_connection() as connection:
            if not include_tools:
                row = connection.execute(
                    """
                    SELECT id, name, description
                    FROM tool_group
                    WHERE id = ?
                    """,
                    (group_id,),
                ).fetchone()
                if row is None:
                    return None
                return ToolGroupSQL._tool_group_without_tools(row)

            rows = connection.execute(
                """
                SELECT
                    tool_group.id AS group_id,
                    tool_group.name AS group_name,
                    tool_group.description AS group_description,
                    tool.id AS tool_id,
                    tool.name AS tool_name,
                    tool.description AS tool_description,
                    tool.input_schema_json AS tool_input_schema_json,
                    tool.output_schema_json AS tool_output_schema_json,
                    adaptor.name AS tool_adaptor_name,
                    mcp_server.url AS server_url
                FROM tool_group
                JOIN mcp_server ON mcp_server.id = tool_group.server_id
                LEFT JOIN tool
                    ON tool.group_id = tool_group.id
                   AND tool.server_id = tool_group.server_id
                LEFT JOIN adaptor ON adaptor.id = tool.adaptor_id
                WHERE tool_group.id = ?
                ORDER BY tool.id
                """,
                (group_id,),
            ).fetchall()
            groups, _ = ToolGroupSQL._tool_groups_from_joined_rows(rows)
            return groups[0] if groups else None

    @staticmethod
    def list_tool_groups(
        *,
        include_tools: bool = True,
    ) -> tuple[list[ToolGroup], list[int]]:
        """获取全部运行时ToolGroup对象及其数据库ID。"""
        with read_connection() as connection:
            if not include_tools:
                rows = connection.execute(
                    """
                    SELECT id, name, description
                    FROM tool_group
                    ORDER BY id
                    """
                ).fetchall()
                return (
                    [ToolGroupSQL._tool_group_without_tools(row) for row in rows],
                    [int(row["id"]) for row in rows],
                )

            rows = connection.execute(
                """
                SELECT
                    tool_group.id AS group_id,
                    tool_group.name AS group_name,
                    tool_group.description AS group_description,
                    tool.id AS tool_id,
                    tool.name AS tool_name,
                    tool.description AS tool_description,
                    tool.input_schema_json AS tool_input_schema_json,
                    tool.output_schema_json AS tool_output_schema_json,
                    adaptor.name AS tool_adaptor_name,
                    mcp_server.url AS server_url
                FROM tool_group
                JOIN mcp_server ON mcp_server.id = tool_group.server_id
                LEFT JOIN tool
                    ON tool.group_id = tool_group.id
                   AND tool.server_id = tool_group.server_id
                LEFT JOIN adaptor ON adaptor.id = tool.adaptor_id
                ORDER BY tool_group.id, tool.id
                """
            ).fetchall()
            return ToolGroupSQL._tool_groups_from_joined_rows(rows)

    @staticmethod
    def list_tool_groups_by_server(
        server_url: str,
        *,
        include_tools: bool = True,
    ) -> tuple[list[ToolGroup], list[int]]:
        """获取指定MCP Server下的ToolGroup对象及数据库ID。"""
        with read_connection() as connection:
            if not include_tools:
                rows = connection.execute(
                    """
                    SELECT tool_group.id, tool_group.name, tool_group.description
                    FROM tool_group
                    JOIN mcp_server ON mcp_server.id = tool_group.server_id
                    WHERE mcp_server.url = ?
                    ORDER BY tool_group.id
                    """,
                    (server_url,),
                ).fetchall()
                return (
                    [ToolGroupSQL._tool_group_without_tools(row) for row in rows],
                    [int(row["id"]) for row in rows],
                )

            rows = connection.execute(
                """
                SELECT
                    tool_group.id AS group_id,
                    tool_group.name AS group_name,
                    tool_group.description AS group_description,
                    tool.id AS tool_id,
                    tool.name AS tool_name,
                    tool.description AS tool_description,
                    tool.input_schema_json AS tool_input_schema_json,
                    tool.output_schema_json AS tool_output_schema_json,
                    adaptor.name AS tool_adaptor_name,
                    mcp_server.url AS server_url
                FROM tool_group
                JOIN mcp_server ON mcp_server.id = tool_group.server_id
                LEFT JOIN tool
                    ON tool.group_id = tool_group.id
                   AND tool.server_id = tool_group.server_id
                LEFT JOIN adaptor ON adaptor.id = tool.adaptor_id
                WHERE mcp_server.url = ?
                ORDER BY tool_group.id, tool.id
                """,
                (server_url,),
            ).fetchall()
            return ToolGroupSQL._tool_groups_from_joined_rows(rows)
