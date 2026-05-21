import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


@router.get("/tasks")
def tasks(
    storage_path: str = Query(min_length=1),
    profile_id: str = Query(min_length=1),
) -> dict[str, object]:
    database_path = _require_database_path(storage_path)

    try:
        connection = _connect_readonly(database_path)
        rows = connection.execute(
            """
            select *
            from tasks
            where profile_id = ?
            order by created_at desc
            """,
            (profile_id,),
        ).fetchall()
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Could not read tasks: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    return {"tasks": [_map_task(row) for row in rows]}


def _require_database_path(storage_path: str) -> Path:
    database_path = Path(storage_path)

    if not database_path.exists():
        raise HTTPException(status_code=404, detail="Storage database does not exist.")

    return database_path


def _connect_readonly(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    return connection


def _map_task(row: sqlite3.Row) -> dict[str, object]:
    task = {
        "id": row["id"],
        "profileId": row["profile_id"],
        "parentSessionId": row["parent_session_id"],
        "sessionId": row["session_id"],
        "kind": row["task_kind"] or "result_output",
        "title": row["title"],
        "status": row["status"],
        "latestProgress": row["latest_progress"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }

    if row["due_at"]:
        task["dueAt"] = row["due_at"]

    return task
