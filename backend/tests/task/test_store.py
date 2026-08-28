"""验证同步两表 TaskStore 的 schema、FIFO、CAS、问题和事务语义。

测试直接调用标准库 sqlite3 Store，不运行事件循环或 Pi；它固定数据库作为唯一排队
真相，并确保状态、问题与可重放事件共享同一个事务边界，包括 Manager 已验证的 workspace
解析结果及 owner/idempotency 持久字段。
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from backend.task.models import (
    CreateTaskCommand,
    TaskNotFound,
    TaskStateConflict,
    TaskStatus,
    TaskVersionConflict,
)
from backend.task.store import TaskPersistenceError, TaskStore, TaskStoreSchemaError


class TaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "task/task-runtime.sqlite3"
        self.store = TaskStore(self.database_path)
        self.store.initialize()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def command(self, index: int = 1, *, rerun_of=None) -> CreateTaskCommand:
        working = self.root / f"project-{index}"
        working.mkdir(exist_ok=True)
        return CreateTaskCommand(
            title=f"Task {index}",
            prompt=f"Prompt {index}",
            workspace_id=f"project-{index}",
            owner_profile_id="profile-1",
            idempotency_key=f"key-{index}",
            timeout_seconds=1800,
            rerun_of_task_id=rerun_of,
        )

    def create(self, index: int = 1, *, rerun_of=None):
        command = self.command(index, rerun_of=rerun_of)
        return self.store.create_task(command, self.root / f"project-{index}")

    def test_initialize_creates_only_two_tables_and_is_idempotent(self):
        self.store.initialize()

        with sqlite3.connect(self.database_path) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            }
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(tables, {"tasks", "task_events"})
        self.assertEqual(version, 2)

    def test_rejects_legacy_and_unknown_schema_without_mutating_it(self):
        self.store.close()
        self.database_path.unlink()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE schema_migrations(version INTEGER)")
            connection.execute("INSERT INTO schema_migrations VALUES (2)")

        with self.assertRaisesRegex(TaskStoreSchemaError, "back up.*entire Task data"):
            TaskStore(self.database_path).initialize()

        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT version FROM schema_migrations").fetchone()[0],
                2,
            )
            connection.execute("DROP TABLE schema_migrations")
            connection.execute("PRAGMA user_version = 99")

        with self.assertRaisesRegex(TaskStoreSchemaError, "user_version=99"):
            TaskStore(self.database_path).initialize()

    def create_v1_fixture(
        self,
        *,
        timeout_constraint: str = " CHECK(timeout_seconds > 0)",
        queue_index_columns: str = "status, deleted_at, row_id",
    ):
        queued_id = UUID("00000000-0000-4000-8000-000000000001")
        running_id = UUID("00000000-0000-4000-8000-000000000002")
        waiting_id = UUID("00000000-0000-4000-8000-000000000003")
        now = datetime(2026, 8, 20, 12, 0, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        self.store.close()
        self.database_path.unlink()
        with sqlite3.connect(self.database_path) as connection:
            connection.executescript(
                f"""
                CREATE TABLE tasks (
                    row_id INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                    title TEXT NOT NULL, prompt TEXT NOT NULL, working_directory TEXT NOT NULL,
                    timeout_seconds INTEGER NOT NULL{timeout_constraint},
                    rerun_of_task_id TEXT REFERENCES tasks(id),
                    status TEXT NOT NULL CHECK(status IN ('queued','running','waiting','paused','completed','failed','cancelled')),
                    active_runtime_ms INTEGER NOT NULL DEFAULT 0 CHECK(active_runtime_ms >= 0),
                    version INTEGER NOT NULL CHECK(version >= 1), question_id TEXT,
                    question_text TEXT, question_options_json TEXT, question_answer TEXT,
                    result_text TEXT, error_code TEXT, error_message TEXT, pause_reason TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, started_at TEXT,
                    finished_at TEXT, deleted_at TEXT
                );
                CREATE INDEX tasks_queue_idx ON tasks({queue_index_columns});
                CREATE TABLE task_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL REFERENCES tasks(id), task_sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL, occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    UNIQUE(task_id, task_sequence)
                );
                CREATE INDEX task_events_replay_idx ON task_events(task_id, event_id);
                """
            )
            task_rows = (
                (queued_id, "queued", 1, None, None, None),
                (running_id, "running", 2, None, None, now),
                (waiting_id, "waiting", 3, "00000000-0000-4000-8000-000000000004", "Choose", now),
            )
            for task_id, status, version, question_id, question_text, started_at in task_rows:
                connection.execute(
                    """INSERT INTO tasks(
                        id,title,prompt,working_directory,timeout_seconds,status,active_runtime_ms,
                        version,question_id,question_text,question_options_json,created_at,updated_at,started_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(task_id), f"{status} task", "Prompt", "/tmp/project", 1800,
                        status, 10, version, question_id, question_text,
                        '["A","B"]' if question_id else None, now, now, started_at,
                    ),
                )
                connection.execute(
                    "INSERT INTO task_events(task_id,task_sequence,event_type,occurred_at,payload_json) VALUES (?,?,?,?,?)",
                    (str(task_id), 1, "task.created", now, '{"status":"queued"}'),
                )
            connection.execute("PRAGMA user_version = 1")
        return queued_id, running_id, waiting_id

    def test_migrates_full_v1_schema_preserving_tasks_events_and_recovery_rows(self):
        queued_id, running_id, waiting_id = self.create_v1_fixture()
        migrated = TaskStore(self.database_path)
        migrated.initialize()
        self.addCleanup(migrated.close)

        queued = migrated.get_task(queued_id)
        running = migrated.get_task(running_id)
        waiting = migrated.get_task(waiting_id)
        recoverable = migrated.recoverable_tasks()
        events = migrated.events_after(0, None, 10)
        self.store.close()
        with sqlite3.connect(self.database_path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(version, 2)
        self.assertTrue({"workspace_id", "owner_profile_id", "idempotency_key"} <= columns)
        self.assertEqual((queued.status, running.status, waiting.status), (
            TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING,
        ))
        self.assertTrue(all(task.workspace_id == "legacy" for task in recoverable))
        self.assertEqual({task.id for task in recoverable}, {queued_id, running_id, waiting_id})
        self.assertEqual([event.task_id for event in events], [queued_id, running_id, waiting_id])

    def test_rejects_incomplete_v1_schema_without_bumping_user_version(self):
        self.store.close()
        self.database_path.unlink()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
            connection.execute("CREATE TABLE task_events (event_id INTEGER PRIMARY KEY)")
            connection.execute("PRAGMA user_version = 1")

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_rejects_v1_schema_missing_required_queue_index(self):
        self.create_v1_fixture()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP INDEX tasks_queue_idx")

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()

    def test_rejects_v1_schema_with_same_named_index_on_wrong_columns(self):
        self.create_v1_fixture(queue_index_columns="row_id, status, deleted_at")

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_rejects_v1_schema_with_same_columns_but_missing_check_constraint(self):
        self.create_v1_fixture(timeout_constraint="")

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_rejects_v1_schema_with_block_commented_check_constraint(self):
        self.create_v1_fixture(timeout_constraint=" /* CHECK(timeout_seconds > 0) */")

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_rejects_v1_schema_with_line_commented_check_constraint(self):
        self.create_v1_fixture(
            timeout_constraint=" -- CHECK(timeout_seconds > 0)\n                    "
        )

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_rejects_v1_schema_with_quoted_check_text_forgery(self):
        self.create_v1_fixture(
            timeout_constraint=" CHECK('check(timeout_seconds>0)'='check(timeout_seconds>0)')"
        )

        with self.assertRaisesRegex(TaskStoreSchemaError, "legacy Task database schema"):
            TaskStore(self.database_path).initialize()
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_sql_normalizer_preserves_quoted_comment_markers(self):
        sql = (
            "CREATE TABLE \"--tasks\" (note TEXT DEFAULT '/* Literal -- Text */', "
            "[--Tag] TEXT) /* real comment */"
        )

        self.assertEqual(
            TaskStore._comment_free_normalized_sql(sql),
            "createtable\"--tasks\"(notetextdefault'/* Literal -- Text */',[--Tag]text)",
        )

    def test_structural_sql_tokens_keep_doubled_quoted_content_as_single_tokens(self):
        sql = (
            "CREATE TABLE `a``--name` (\"a\"\"--tag\" TEXT "
            "DEFAULT 'it''s -- text')"
        )

        self.assertEqual(
            TaskStore._structural_sql_tokens(sql),
            (
                ("word", "create"), ("word", "table"), ("identifier", "a`--name"),
                ("symbol", "("), ("identifier", 'a"--tag'), ("word", "text"),
                ("word", "default"), ("string", "it's -- text"), ("symbol", ")"),
            ),
        )

    def test_rejects_v2_schema_with_partial_idempotency_index_missing_predicate(self):
        self.store.close()
        with sqlite3.connect(self.database_path) as connection:
            connection.execute("DROP INDEX tasks_owner_idempotency_idx")
            connection.execute(
                "CREATE UNIQUE INDEX tasks_owner_idempotency_idx "
                "ON tasks(owner_profile_id, idempotency_key)"
            )

        with self.assertRaisesRegex(TaskStoreSchemaError, "current Task database schema"):
            TaskStore(self.database_path).initialize()

    def test_create_list_and_claim_use_sqlite_fifo(self):
        tasks = [self.create(index) for index in range(1, 7)]

        claimed = self.store.claim_queued(5)
        page = self.store.list_tasks(limit=10)

        self.assertEqual([task.id for task in claimed], [task.id for task in tasks[:5]])
        self.assertTrue(all(task.status == TaskStatus.RUNNING for task in claimed))
        self.assertEqual(page.items[-1].status, TaskStatus.QUEUED)
        self.assertTrue(all(task.started_at is not None for task in claimed))

    def test_transition_cas_and_event_failure_rollback_together(self):
        task = self.create()
        running = self.store.claim_queued(1)[0]

        with self.assertRaises(TaskVersionConflict):
            self.store.transition_task(
                task.id,
                TaskStatus.PAUSED,
                expected_version=task.version,
            )
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_status_event
                BEFORE INSERT ON task_events
                WHEN NEW.event_type = 'task.status_changed'
                BEGIN SELECT RAISE(ABORT, 'event rejected'); END
                """
            )
        with self.assertRaises(TaskPersistenceError):
            self.store.transition_task(
                task.id,
                TaskStatus.PAUSED,
                expected_version=running.version,
            )

        unchanged = self.store.get_task(task.id)
        self.assertEqual(unchanged.status, TaskStatus.RUNNING)
        self.assertEqual(unchanged.version, running.version)

    def test_question_is_folded_into_task_and_answer_returns_queued(self):
        task = self.create()
        running = self.store.claim_queued(1)[0]

        waiting, raised = self.store.raise_question(
            task.id,
            "Choose",
            ("A", "B"),
            expected_version=running.version,
            active_runtime_ms=123,
        )
        answered, answer_event = self.store.answer_question(
            task.id,
            waiting.question_id,
            "A",
            expected_version=waiting.version,
        )

        self.assertEqual(waiting.status, TaskStatus.WAITING)
        self.assertEqual(waiting.question_options, ("A", "B"))
        self.assertEqual(raised.type, "task.question_raised")
        self.assertEqual(answered.status, TaskStatus.QUEUED)
        self.assertEqual(answered.question_answer, "A")
        self.assertEqual(answer_event.type, "task.question_answered")

        resumed = self.store.claim_queued(1)[0]
        cleared = self.store.clear_question(
            task.id,
            expected_version=resumed.version,
        )
        self.assertIsNone(cleared.question_id)
        self.assertIsNone(cleared.question_answer)

    def test_rerun_and_tombstone_keep_visibility_contract(self):
        source = self.create()
        with self.assertRaises(TaskStateConflict):
            self.create(2, rerun_of=source.id)
        source, _ = self.store.transition_task(
            source.id,
            TaskStatus.CANCELLED,
            expected_version=source.version,
        )
        rerun = self.create(3, rerun_of=source.id)
        self.assertEqual(rerun.rerun_of_task_id, source.id)

        deleted, event = self.store.tombstone_task(source.id)
        repeated, repeated_event = self.store.tombstone_task(source.id)
        self.assertIsNotNone(deleted.deleted_at)
        self.assertEqual(repeated, deleted)
        self.assertEqual(repeated_event.event_id, event.event_id)
        self.assertIn(source.id, self.store.deleted_task_ids())
        with self.assertRaises(TaskNotFound):
            self.store.get_task(source.id)
