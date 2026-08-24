"""在调用方事务内持久化 Task Question 的提出、回答与投递。

本模块集中维护 task_questions 与对应 Task 状态、版本和业务事件的跨表写入；所有函数
接收 TaskStore 当前的 SQLite connection，不创建连接、不获取写锁也不提交事务。这样
Question workflow 可以从 Store 门面移出，同时继续与 Task 状态和 Event 原子提交。
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import aiosqlite

from ..models import (
    TaskEvent,
    TaskNotFound,
    TaskQuestion,
    TaskRecord,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
    validate_status_transition,
)
from .codec import dump_time, now, question_from_row, task_from_row
from .events import insert_event, next_sequence


async def raise_question(
    connection: aiosqlite.Connection,
    task_id: UUID,
    question: str,
    options: tuple[str, ...],
    rpc_request_id: str | None,
    *,
    expected_version: int,
    active_runtime_ms: int | None,
) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
    row = await _task_row(connection, task_id)
    actual = int(row["version"])
    if actual != expected_version:
        raise TaskVersionConflict(expected_version, actual)
    current = TaskStatus(row["status"])
    validate_status_transition(current, TaskStatus.WAITING)

    question_id = uuid4()
    occurred_at = now()
    await connection.execute(
        """
        INSERT INTO task_questions(
            id, task_id, question, options_json, rpc_request_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(question_id),
            str(task_id),
            question,
            json.dumps(options, ensure_ascii=False),
            rpc_request_id,
            dump_time(occurred_at),
        ),
    )
    await connection.execute(
        """
        UPDATE tasks SET status = ?, active_runtime_ms = ?, version = ?, updated_at = ?
        WHERE id = ? AND version = ?
        """,
        (
            TaskStatus.WAITING.value,
            active_runtime_ms
            if active_runtime_ms is not None
            else int(row["active_runtime_ms"]),
            actual + 1,
            dump_time(occurred_at),
            str(task_id),
            actual,
        ),
    )
    event = await insert_event(
        connection,
        task_id,
        await next_sequence(connection, task_id),
        "task.question_raised",
        occurred_at,
        {
            "questionId": str(question_id),
            "question": question,
            "options": list(options),
        },
    )
    return (
        task_from_row(await _task_row(connection, task_id)),
        await get_question(connection, question_id),
        event,
    )


async def answer_question(
    connection: aiosqlite.Connection,
    task_id: UUID,
    question_id: UUID,
    answer: str,
    *,
    expected_version: int,
) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
    row = await _task_row(connection, task_id)
    actual = int(row["version"])
    if actual != expected_version:
        raise TaskVersionConflict(expected_version, actual)
    current = TaskStatus(row["status"])
    if current != TaskStatus.WAITING:
        raise TaskStateConflict(current, TaskStatus.WAITING)
    pending = await (
        await connection.execute(
            """
            SELECT * FROM task_questions
            WHERE id = ? AND task_id = ? AND answered_at IS NULL
            """,
            (str(question_id), str(task_id)),
        )
    ).fetchone()
    if pending is None:
        raise TaskNotFound(f"Pending Task question not found: {question_id}")

    occurred_at = now()
    await connection.execute(
        "UPDATE task_questions SET answer = ?, answered_at = ? WHERE id = ?",
        (answer, dump_time(occurred_at), str(question_id)),
    )
    await connection.execute(
        "UPDATE tasks SET version = ?, updated_at = ? WHERE id = ? AND version = ?",
        (actual + 1, dump_time(occurred_at), str(task_id), actual),
    )
    event = await insert_event(
        connection,
        task_id,
        await next_sequence(connection, task_id),
        "task.question_answered",
        occurred_at,
        {"questionId": str(question_id), "answer": answer},
    )
    return (
        task_from_row(await _task_row(connection, task_id)),
        await get_question(connection, question_id),
        event,
    )


async def deliver_answer_and_transition(
    connection: aiosqlite.Connection,
    task_id: UUID,
    question_id: UUID,
    target_status: TaskStatus,
    *,
    expected_version: int,
) -> tuple[TaskRecord, TaskQuestion, TaskEvent]:
    row = await _task_row(connection, task_id)
    actual = int(row["version"])
    if actual != expected_version:
        raise TaskVersionConflict(expected_version, actual)
    current = TaskStatus(row["status"])
    validate_status_transition(current, target_status)
    answered = await (
        await connection.execute(
            """SELECT * FROM task_questions
               WHERE id = ? AND task_id = ? AND answered_at IS NOT NULL""",
            (str(question_id), str(task_id)),
        )
    ).fetchone()
    if answered is None:
        raise TaskNotFound(f"Answered Task question not found: {question_id}")

    occurred_at = now()
    await connection.execute(
        "UPDATE task_questions SET delivered_at = COALESCE(delivered_at, ?) WHERE id = ?",
        (dump_time(occurred_at), str(question_id)),
    )
    await connection.execute(
        """
        UPDATE tasks SET status = ?, version = ?, updated_at = ?
        WHERE id = ? AND version = ?
        """,
        (
            target_status.value,
            actual + 1,
            dump_time(occurred_at),
            str(task_id),
            actual,
        ),
    )
    event = await insert_event(
        connection,
        task_id,
        await next_sequence(connection, task_id),
        "task.status_changed",
        occurred_at,
        {"from": current.value, "to": target_status.value},
    )
    return (
        task_from_row(await _task_row(connection, task_id)),
        await get_question(connection, question_id),
        event,
    )


async def pending_question(
    connection: aiosqlite.Connection, task_id: UUID
) -> TaskQuestion | None:
    row = await (
        await connection.execute(
            """
            SELECT * FROM task_questions
            WHERE task_id = ? AND answered_at IS NULL
            ORDER BY created_at LIMIT 1
            """,
            (str(task_id),),
        )
    ).fetchone()
    return question_from_row(row) if row else None


async def undelivered_answer(
    connection: aiosqlite.Connection, task_id: UUID
) -> TaskQuestion | None:
    row = await (
        await connection.execute(
            """
            SELECT * FROM task_questions
            WHERE task_id = ? AND answered_at IS NOT NULL AND delivered_at IS NULL
            ORDER BY answered_at LIMIT 1
            """,
            (str(task_id),),
        )
    ).fetchone()
    return question_from_row(row) if row else None


async def get_question(
    connection: aiosqlite.Connection, question_id: UUID
) -> TaskQuestion:
    row = await (
        await connection.execute(
            "SELECT * FROM task_questions WHERE id = ?", (str(question_id),)
        )
    ).fetchone()
    if row is None:
        raise TaskNotFound(f"Task question not found: {question_id}")
    return question_from_row(row)


async def _task_row(
    connection: aiosqlite.Connection, task_id: UUID
) -> aiosqlite.Row:
    row = await (
        await connection.execute(
            "SELECT * FROM tasks WHERE id = ? AND deleted_at IS NULL",
            (str(task_id),),
        )
    ).fetchone()
    if row is None:
        raise TaskNotFound(f"Task not found: {task_id}")
    return row
