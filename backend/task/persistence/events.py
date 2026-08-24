"""读写 Task 的可重放业务事件并维护单 Task sequence。

写入函数始终接收调用方现有的 SQLite transaction connection，因此状态、Question 与 Event
可以在同一 rollback boundary 中提交。此模块不拥有 connection、write lock 或 commit。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

import aiosqlite

from ..models import TaskEvent
from .codec import dump_time, event_from_row


async def next_sequence(connection: aiosqlite.Connection, task_id: UUID) -> int:
    row = await (
        await connection.execute(
            """
            SELECT COALESCE(MAX(task_sequence), 0) + 1 AS next_sequence
            FROM task_events WHERE task_id = ?
            """,
            (str(task_id),),
        )
    ).fetchone()
    return int(row["next_sequence"])


async def insert_event(
    connection: aiosqlite.Connection,
    task_id: UUID,
    sequence: int,
    event_type: str,
    occurred_at: datetime,
    payload: Mapping[str, Any],
) -> TaskEvent:
    cursor = await connection.execute(
        """
        INSERT INTO task_events(
            task_id, task_sequence, event_type, occurred_at, payload_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            str(task_id),
            sequence,
            event_type,
            dump_time(occurred_at),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    return TaskEvent(
        event_id=int(cursor.lastrowid),
        task_sequence=sequence,
        task_id=task_id,
        type=event_type,
        occurred_at=occurred_at,
        payload=dict(payload),
    )


async def events_after(
    connection: aiosqlite.Connection,
    event_id: int,
    task_id: UUID | None,
    limit: int,
) -> list[TaskEvent]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    parameters: list[Any] = [event_id]
    clause = "event_id > ?"
    if task_id is not None:
        clause += " AND task_id = ?"
        parameters.append(str(task_id))
    parameters.append(limit)
    rows = await (
        await connection.execute(
            f"SELECT * FROM task_events WHERE {clause} ORDER BY event_id LIMIT ?",
            parameters,
        )
    ).fetchall()
    return [event_from_row(row) for row in rows]
