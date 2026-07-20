import sqlite3
from typing import TypedDict
from urllib.parse import parse_qsl, urlsplit

from .utils import read_connection, transaction


class MCPServerMetadata(TypedDict):
    name: str
    url: str
    description: str | None


class MCPServerSQL:
    """无需实例化的MCP Server SQLite工具类。"""

    _SENSITIVE_QUERY_KEYS = {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "key",
        "password",
        "secret",
        "token",
    }

    @staticmethod
    def _mcp_server_from_row(row: sqlite3.Row) -> MCPServerMetadata:
        return {
            "name": row["name"],
            "url": row["url"],
            "description": row["description"],
        }

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Reject empty URLs and credentials embedded in an MCP endpoint URL."""
        if not url or url != url.strip() or any(character.isspace() for character in url):
            return False

        candidate = url if "://" in url else f"//{url}"
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
            parsed.port
        except ValueError:
            return False

        if (
            not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            return False

        query_keys = {
            key.casefold().replace("-", "_")
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        }
        return not query_keys.intersection(MCPServerSQL._SENSITIVE_QUERY_KEYS)

    @staticmethod
    def insert_mcp_server_metadata(
        name: str,
        url: str,
        description: str | None = None,
    ) -> int | None:
        """
        写入一条MCP Server元数据。

        ret:
            int: 新创建的MCP Server ID
            None: 名称/URL无效、名称重复、URL重复或约束校验失败
        """
        if not name or not MCPServerSQL._is_safe_url(url):
            return None

        try:
            with transaction() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO mcp_server (name, url, description)
                    VALUES (?, ?, ?)
                    """,
                    (name, url, description),
                )
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    @staticmethod
    def delete_mcp_server_metadata(url: str) -> bool:
        """删除没有ToolGroup的MCP Server。"""
        try:
            with transaction() as connection:
                row = connection.execute(
                    """
                    SELECT
                        mcp_server.id,
                        EXISTS (
                            SELECT 1
                            FROM tool_group
                            WHERE tool_group.server_id = mcp_server.id
                        ) AS has_tool_groups
                    FROM mcp_server
                    WHERE mcp_server.url = ?
                    """,
                    (url,),
                ).fetchone()
                if row is None or bool(row["has_tool_groups"]):
                    return False

                cursor = connection.execute(
                    "DELETE FROM mcp_server WHERE id = ?",
                    (row["id"],),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def update_mcp_server_metadata(
        url: str,
        *,
        new_name: str | None = None,
        new_url: str | None = None,
        description: str | None = None,
    ) -> bool:
        """
        更新给定MCP Server元数据。

        None表示不更新对应字段。
        """
        if new_name is not None and not new_name:
            return False
        if new_url is not None and not MCPServerSQL._is_safe_url(new_url):
            return False

        try:
            with transaction() as connection:
                row = connection.execute(
                    "SELECT id FROM mcp_server WHERE url = ?",
                    (url,),
                ).fetchone()
                if row is None:
                    return False

                updates: list[str] = []
                values: list[object] = []

                if new_name is not None:
                    updates.append("name = ?")
                    values.append(new_name)
                if new_url is not None:
                    updates.append("url = ?")
                    values.append(new_url)
                if description is not None:
                    updates.append("description = ?")
                    values.append(description)

                if not updates:
                    return True

                updates.append("updated_at = CURRENT_TIMESTAMP")
                values.append(row["id"])

                cursor = connection.execute(
                    f"UPDATE mcp_server SET {', '.join(updates)} WHERE id = ?",
                    values,
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    @staticmethod
    def get_mcp_server(url: str) -> MCPServerMetadata | None:
        """按URL获取MCP Server元数据。"""
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT name, url, description
                FROM mcp_server
                WHERE url = ?
                """,
                (url,),
            ).fetchone()
            if row is None:
                return None
            return MCPServerSQL._mcp_server_from_row(row)

    @staticmethod
    def get_mcp_server_by_id(server_id: int) -> MCPServerMetadata | None:
        """按数据库ID获取MCP Server元数据。"""
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT name, url, description
                FROM mcp_server
                WHERE id = ?
                """,
                (server_id,),
            ).fetchone()
            if row is None:
                return None
            return MCPServerSQL._mcp_server_from_row(row)

    @staticmethod
    def get_mcp_server_by_name(name: str) -> MCPServerMetadata | None:
        """按全局唯一名称获取MCP Server元数据。"""
        with read_connection() as connection:
            row = connection.execute(
                """
                SELECT name, url, description
                FROM mcp_server
                WHERE name = ?
                """,
                (name,),
            ).fetchone()
            if row is None:
                return None
            return MCPServerSQL._mcp_server_from_row(row)

    @staticmethod
    def list_mcp_servers() -> tuple[list[MCPServerMetadata], list[int]]:
        """获取全部MCP Server元数据及其数据库ID。"""
        with read_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, name, url, description
                FROM mcp_server
                ORDER BY id
                """
            ).fetchall()
            return (
                [MCPServerSQL._mcp_server_from_row(row) for row in rows],
                [int(row["id"]) for row in rows],
            )
