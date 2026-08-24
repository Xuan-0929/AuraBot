"""转换 Task SQLite 行、时间和 JSON 为稳定的产品模型。

这些函数不执行 SQL、不访问 Store，也不提交事务；Store、Question 与 Event 模块共享它们，
避免各自复制数据库表示到 TaskRecord、TaskQuestion 和 TaskEvent 的映射规则。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import aiosqlite

from ..models import TaskErrorCode, TaskEvent, TaskQuestion, TaskRecord, TaskStatus


def now() -> datetime:
    return datetime.now(UTC)


def dump_time(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def load_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def task_from_row(row: aiosqlite.Row) -> TaskRecord:
    return TaskRecord(
        id=UUID(row["id"]),
        row_id=int(row["row_id"]),
        title=row["title"],
        prompt=row["prompt"],
        working_directory=Path(row["working_directory"]),
        status=TaskStatus(row["status"]),
        timeout_seconds=int(row["timeout_seconds"]),
        active_runtime_ms=int(row["active_runtime_ms"]),
        pi_session_id=row["pi_session_id"],
        pi_session_path=Path(row["pi_session_path"])
        if row["pi_session_path"]
        else None,
        result_text=row["result_text"],
        error_code=TaskErrorCode(row["error_code"]) if row["error_code"] else None,
        error_message=row["error_message"],
        pause_reason=row["pause_reason"],
        rerun_of_task_id=UUID(row["rerun_of_task_id"])
        if row["rerun_of_task_id"]
        else None,
        version=int(row["version"]),
        created_at=load_time(row["created_at"]),
        updated_at=load_time(row["updated_at"]),
        started_at=load_time(row["started_at"]),
        finished_at=load_time(row["finished_at"]),
        deleted_at=load_time(row["deleted_at"]),
    )


def event_from_row(row: aiosqlite.Row) -> TaskEvent:
    return TaskEvent(
        event_id=int(row["event_id"]),
        task_sequence=int(row["task_sequence"]),
        task_id=UUID(row["task_id"]),
        type=row["event_type"],
        occurred_at=load_time(row["occurred_at"]),
        payload=json.loads(row["payload_json"]),
    )


def question_from_row(row: aiosqlite.Row) -> TaskQuestion:
    return TaskQuestion(
        id=UUID(row["id"]),
        task_id=UUID(row["task_id"]),
        question=row["question"],
        options=tuple(json.loads(row["options_json"])),
        rpc_request_id=row["rpc_request_id"],
        answer=row["answer"],
        created_at=load_time(row["created_at"]),
        answered_at=load_time(row["answered_at"]),
        delivered_at=load_time(row["delivered_at"]),
    )
