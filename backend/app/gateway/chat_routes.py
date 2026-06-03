import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/chat")


@router.get("/main-session")
def main_session_messages(
    storage_path: str = Query(min_length=1),
    profile_id: str = Query(min_length=1),
) -> dict[str, object]:
    database_path = _require_database_path(storage_path)

    try:
        connection = _connect_readonly(database_path)
        session = connection.execute(
            """
            select id
            from sessions
            where profile_id = ? and kind = 'main'
            limit 1
            """,
            (profile_id,),
        ).fetchone()

        if not session:
            raise HTTPException(status_code=404, detail="Main chat session does not exist.")

        messages = connection.execute(
            """
            select id, session_id, profile_id, role, content, metadata_json, created_at
            from messages
            where session_id = ?
            order by created_at asc, rowid asc
            """,
            (session["id"],),
        ).fetchall()
    except HTTPException:
        raise
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Could not read main session messages: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    return {
        "sessionId": session["id"],
        "messages": [_map_message(row) for row in messages],
    }


def _require_database_path(storage_path: str) -> Path:
    database_path = Path(storage_path)

    if not database_path.exists():
        raise HTTPException(status_code=404, detail="Storage database does not exist.")

    return database_path


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    return connection


def _map_message(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "profileId": row["profile_id"],
        "role": row["role"],
        "content": row["content"],
        "attachments": _parse_message_attachments(row["metadata_json"]),
        "createdAt": row["created_at"],
    }


def _parse_message_attachments(metadata_json: str | None) -> list[object]:
    if not metadata_json:
        return []

    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError:
        return []

    attachments = metadata.get("attachments") if isinstance(metadata, dict) else None

    return attachments if isinstance(attachments, list) else []
