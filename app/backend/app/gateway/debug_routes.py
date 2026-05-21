import json
import sqlite3
from datetime import UTC, datetime
from os.path import basename
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/debug")


@router.get("/events")
def debug_events(
    storage_path: str = Query(min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, object]:
    database_path = _require_database_path(storage_path)

    try:
        connection = _connect_readonly(database_path)
        rows = connection.execute(
            """
            select id, event_type, payload_json, created_at
            from event_logs
            order by created_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Could not read event logs: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    return {"events": [_map_event(row) for row in rows]}


@router.get("/memory")
def debug_memory(
    storage_path: str = Query(min_length=1),
    profile_id: str = Query(min_length=1),
    event_limit: int = Query(default=80, ge=1, le=500),
) -> dict[str, object]:
    database_path = _require_database_path(storage_path)

    try:
        connection = _connect_readonly(database_path)
        nodes = connection.execute(
            """
            select *
            from memory_nodes
            where profile_id = ? and state != 'archived'
            order by importance desc, updated_at desc
            """,
            (profile_id,),
        ).fetchall()
        edges = connection.execute(
            "select * from memory_edges where profile_id = ?",
            (profile_id,),
        ).fetchall()
        summaries = connection.execute(
            "select * from memory_summaries where profile_id = ?",
            (profile_id,),
        ).fetchall()
        events = connection.execute(
            """
            select id, event_type, payload_json, created_at
            from event_logs
            where event_type like 'memory.%'
            order by created_at desc
            limit ?
            """,
            (event_limit,),
        ).fetchall()
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Could not read memory debug data: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    return {
        "generatedAt": _utc_now_iso(),
        "nodes": [_map_memory_node(row) for row in nodes],
        "edges": [_map_memory_edge(row) for row in edges],
        "summaries": [_map_memory_summary(row) for row in summaries],
        "recentEvents": [_map_event(row) for row in events],
    }


@router.get("/tasks")
def debug_tasks(
    storage_path: str = Query(min_length=1),
    profile_id: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, object]:
    database_path = _require_database_path(storage_path)

    try:
        connection = _connect_readonly(database_path)
        task_rows = connection.execute(
            """
            select *
            from tasks
            where profile_id = ?
            order by created_at desc
            limit ?
            """,
            (profile_id, limit),
        ).fetchall()
        task_ids = [row["id"] for row in task_rows]
        events_by_task = _group_rows_by_task(connection, "task_events", task_ids)
        artifacts_by_task = _group_rows_by_task(connection, "task_artifacts", task_ids)
        input_artifacts_by_task = _group_rows_by_task(connection, "task_input_artifacts", task_ids)
    except sqlite3.Error as error:
        raise HTTPException(status_code=500, detail=f"Could not read task debug data: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()

    return {
        "generatedAt": _utc_now_iso(),
        "tasks": [
            _map_task_detail(
                task,
                events_by_task.get(task["id"], []),
                artifacts_by_task.get(task["id"], []),
                input_artifacts_by_task.get(task["id"], []),
            )
            for task in task_rows
        ],
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


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _map_event(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "eventType": row["event_type"],
        "payload": json.loads(row["payload_json"]),
        "createdAt": row["created_at"],
    }


def _map_memory_node(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "type": row["type"],
        "state": row["state"],
        "content": row["content"],
        "importance": row["importance"],
        "startsAt": row["starts_at"],
        "endsAt": row["ends_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _map_memory_edge(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "sourceNodeId": row["source_node_id"],
        "targetNodeId": row["target_node_id"],
        "relationType": row["relation_type"],
        "weight": row["weight"],
        "createdAt": row["created_at"],
    }


def _map_memory_summary(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "topic": row["topic"],
        "content": row["content"],
        "updatedAt": row["updated_at"],
    }


def _group_rows_by_task(
    connection: sqlite3.Connection,
    table_name: str,
    task_ids: list[str],
) -> dict[str, list[sqlite3.Row]]:
    if not task_ids:
        return {}

    placeholders = ",".join("?" for _ in task_ids)
    rows = connection.execute(
        f"select * from {table_name} where task_id in ({placeholders}) order by created_at asc",
        task_ids,
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}

    for row in rows:
        grouped.setdefault(row["task_id"], []).append(row)

    return grouped


def _map_task_detail(
    task: sqlite3.Row,
    events: list[sqlite3.Row],
    artifacts: list[sqlite3.Row],
    input_artifacts: list[sqlite3.Row],
) -> dict[str, object]:
    timeline = [_map_task_timeline_item(event) for event in events]

    return {
        "task": _map_task(task),
        "inputArtifacts": [_map_task_input_artifact(row) for row in input_artifacts],
        "artifacts": [_map_task_artifact(row) for row in artifacts],
        "timeline": timeline,
        "recentError": _find_recent_task_error(task, events),
        "startedAt": _first_timeline_string(timeline, "startedAt"),
        "completedAt": _last_timeline_string(timeline, "completedAt"),
        "durationMs": _last_timeline_number(timeline, "durationMs"),
    }


def _map_task(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "profileId": row["profile_id"],
        "parentSessionId": row["parent_session_id"],
        "sessionId": row["session_id"],
        "kind": row["task_kind"] or "result_output",
        "title": row["title"],
        "status": row["status"],
        "latestProgress": row["latest_progress"],
        "dueAt": row["due_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _map_task_timeline_item(row: sqlite3.Row) -> dict[str, object]:
    payload = json.loads(row["payload_json"])
    summary = payload.get("summary") if isinstance(payload.get("summary"), str) else row["event_type"]

    return {
        "id": row["id"],
        "eventType": row["event_type"],
        "summary": summary,
        "createdAt": row["created_at"],
        "startedAt": _read_string(payload.get("startedAt")),
        "completedAt": _read_string(payload.get("completedAt")),
        "durationMs": _read_number(payload.get("durationMs")),
        "payloadSummary": _summarize_task_payload(payload),
    }


def _map_task_artifact(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "artifactType": row["artifact_type"],
        "uri": row["uri"],
        "name": basename(row["uri"]),
        "createdAt": row["created_at"],
    }


def _map_task_input_artifact(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "taskId": row["task_id"],
        "artifactType": row["artifact_type"],
        "originalName": row["original_name"],
        "mimeType": row["mime_type"],
        "sizeBytes": row["size_bytes"],
        "uri": row["uri"],
        "name": row["original_name"],
        "createdAt": row["created_at"],
    }


def _summarize_task_payload(payload: dict[str, object]) -> dict[str, object]:
    summary: dict[str, object] = {}

    for key, value in payload.items():
        if key in {"stdout", "stderr"}:
            if isinstance(value, str):
                summary[f"{key}Bytes"] = len(value.encode("utf-8"))
            continue
        if isinstance(value, str | int | float | bool):
            summary[key] = value

    return summary


def _find_recent_task_error(task: sqlite3.Row, events: list[sqlite3.Row]) -> str | None:
    if task["status"] not in {"failed", "interrupted"}:
        return None

    for event in reversed(events):
        if event["event_type"] in {"task.failed", "task.agent.cancelled"} or "failed" in event["event_type"]:
            payload = json.loads(event["payload_json"])
            if isinstance(payload.get("summary"), str):
                return payload["summary"]
            return event["event_type"]

    return None


def _first_timeline_string(items: list[dict[str, object]], key: str) -> str | None:
    for item in items:
        value = item.get(key)
        if isinstance(value, str):
            return value
    return None


def _last_timeline_string(items: list[dict[str, object]], key: str) -> str | None:
    for item in reversed(items):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return None


def _last_timeline_number(items: list[dict[str, object]], key: str) -> int | float | None:
    for item in reversed(items):
        value = item.get(key)
        if isinstance(value, int | float):
            return value
    return None


def _read_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _read_number(value: object) -> int | float | None:
    return value if isinstance(value, int | float) else None
