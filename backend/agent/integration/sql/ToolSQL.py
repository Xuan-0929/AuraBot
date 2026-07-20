import sqlite3

from ...tools.Tool import Tool
from ...tools.utils.formatter import (
    parse_optional_schema_json,
    parse_schema_json,
    serialize_schema,
)
from .utils import read_connection, transaction


"""
用于存放Tool系统与SQLite交互的工具函数。

这一层直接与SQLite交互。
"""


class _Unset:
    pass


_UNSET = _Unset()


class ToolSQL:
    """无需实例化的Tool SQLite工具类。"""

    @staticmethod
    def _tool_from_row(row: sqlite3.Row) -> Tool:
        return Tool(
            name=row["name"],
            description=row["description"],
            server_url=row["server_url"],
            input_schema=parse_schema_json(row["input_schema_json"]),
            output_schema=parse_optional_schema_json(
                row["output_schema_json"]
            ),
        )

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
    def _get_group(
        connection: sqlite3.Connection,
        group_name: str,
    ) -> tuple[int, int] | None:
        row = connection.execute(
            "SELECT id, server_id FROM tool_group WHERE name = ?",
            (group_name,),
        ).fetchone()
        if row is None:
            return None
        return int(row["id"]), int(row["server_id"])

    @staticmethod
    def insert_tool_metadata(
        name: str,
        description: str,
        input_schema: dict,
        output_schema: dict | None,
        group_name: str,
        server_url: str,
    ) -> int | None:
        """
        写入一条Tool元数据。

        ret:
            int: 新创建的Tool ID
            None: Server/Group不存在、二者不匹配、名称重复或schema非法
        """
        try:
            input_schema_json = serialize_schema(input_schema)
            output_schema_json = (
                serialize_schema(output_schema)
                if output_schema is not None
                else None
            )
        except (TypeError, ValueError):
            return None

        try:
            with transaction() as connection:
                server_id = ToolSQL._get_server_id(connection, server_url)
                group = ToolSQL._get_group(connection, group_name)
                if server_id is None or group is None:
                    return None

                group_id, group_server_id = group
                if server_id != group_server_id:
                    return None

                cursor = connection.execute(
                    """
                    INSERT INTO tool (
                        server_id,
                        group_id,
                        name,
                        description,
                        input_schema_json,
                        output_schema_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        server_id,
                        group_id,
                        name,
                        description,
                        input_schema_json,
                        output_schema_json,
                    ),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def delete_tool_metadata(server_url: str, name: str) -> bool:
        """使用MCP Server URL和Tool名称删除一条记录。"""
        with transaction() as connection:
            row = connection.execute(
                """
                SELECT tool.id
                FROM tool
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE mcp_server.url = ?
                  AND tool.name = ?
                """,
                (server_url, name),
            ).fetchone()
            if row is None:
                return False

            cursor = connection.execute(
                "DELETE FROM tool WHERE id = ?",
                (row["id"],),
            )
            return cursor.rowcount > 0

    @staticmethod
    def update_tool_metadata(
        server_url: str,
        name: str,
        *,
        new_name: str | None = None,
        description: str | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None | _Unset = _UNSET,
        new_group_name: str | None = None,
    ) -> bool:
        """
        更新给定Tool元数据。

        使用server_url + name定位记录。output_schema未传表示不更新；显式传None
        表示将数据库字段清空。Tool只能移动到同一个MCP Server下的ToolGroup。
        """
        try:
            input_schema_json = (
                serialize_schema(input_schema)
                if input_schema is not None
                else None
            )
            output_schema_json = (
                serialize_schema(output_schema)
                if not isinstance(output_schema, _Unset)
                and output_schema is not None
                else None
            )
        except (TypeError, ValueError):
            return False

        try:
            with transaction() as connection:
                row = connection.execute(
                    """
                    SELECT tool.id, tool.server_id
                    FROM tool
                    JOIN mcp_server ON mcp_server.id = tool.server_id
                    WHERE mcp_server.url = ?
                      AND tool.name = ?
                    """,
                    (server_url, name),
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
                if input_schema is not None:
                    updates.append("input_schema_json = ?")
                    values.append(input_schema_json)
                if not isinstance(output_schema, _Unset):
                    updates.append("output_schema_json = ?")
                    values.append(output_schema_json)
                if new_group_name is not None:
                    group = ToolSQL._get_group(connection, new_group_name)
                    if group is None:
                        return False
                    new_group_id, new_group_server_id = group
                    if new_group_server_id != int(row["server_id"]):
                        return False
                    updates.append("group_id = ?")
                    values.append(new_group_id)

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])

                cursor = connection.execute(
                    f"UPDATE tool SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_tool(server_url: str, name: str) -> Tool | None:
        """使用MCP Server URL和Tool名称获取运行时Tool对象。"""
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT
                    tool.name,
                    tool.description,
                    tool.input_schema_json,
                    tool.output_schema_json,
                    mcp_server.url AS server_url
                FROM tool
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE mcp_server.url = ?
                  AND tool.name = ?
                """,
                (server_url, name),
            ).fetchone()
            if row is None:
                return None
            return ToolSQL._tool_from_row(row)

    @staticmethod
    def get_tool_by_id(tool_id: int) -> Tool | None:
        """按数据库ID获取运行时Tool对象。"""
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT
                    tool.name,
                    tool.description,
                    tool.input_schema_json,
                    tool.output_schema_json,
                    mcp_server.url AS server_url
                FROM tool
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE tool.id = ?
                """,
                (tool_id,),
            ).fetchone()
            if row is None:
                return None
            return ToolSQL._tool_from_row(row)

    @staticmethod
    def list_tools(group_name: str) -> tuple[list[Tool], list[int]]:
        """获取指定ToolGroup下的运行时Tool对象及数据库ID。"""
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    tool.id,
                    tool.name,
                    tool.description,
                    tool.input_schema_json,
                    tool.output_schema_json,
                    mcp_server.url AS server_url
                FROM tool
                JOIN tool_group ON tool_group.id = tool.group_id
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE tool_group.name = ?
                  AND tool.server_id = tool_group.server_id
                ORDER BY tool.id
                """,
                (group_name,),
            ).fetchall()
            return (
                [ToolSQL._tool_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )

    @staticmethod
    def list_tools_by_name(name: str) -> tuple[list[Tool], list[int]]:
        """获取所有MCP Server中同名的运行时Tool对象及数据库ID。"""
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    tool.id,
                    tool.name,
                    tool.description,
                    tool.input_schema_json,
                    tool.output_schema_json,
                    mcp_server.url AS server_url
                FROM tool
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE tool.name = ?
                ORDER BY tool.id
                """,
                (name,),
            ).fetchall()
            return (
                [ToolSQL._tool_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )

    @staticmethod
    def list_tools_by_server(
        server_url: str,
    ) -> tuple[list[Tool], list[int]]:
        """获取指定MCP Server下的运行时Tool对象及数据库ID。"""
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT
                    tool.id,
                    tool.name,
                    tool.description,
                    tool.input_schema_json,
                    tool.output_schema_json,
                    mcp_server.url AS server_url
                FROM tool
                JOIN mcp_server ON mcp_server.id = tool.server_id
                WHERE mcp_server.url = ?
                ORDER BY tool.id
                """,
                (server_url,),
            ).fetchall()
            return (
                [ToolSQL._tool_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )
