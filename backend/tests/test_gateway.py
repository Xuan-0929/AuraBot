import unittest
import sqlite3
import os
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.config import config as global_config
from backend.main import app, health, services


class GatewayEndpointTests(unittest.TestCase):
    def test_main_initializes_the_global_config_singleton(self):
        from backend.main import config as main_config

        self.assertIs(main_config, global_config)
        self.assertTrue(main_config.initialized)
        self.assertEqual(main_config["ports"]["backend_gateway"], 6401)

    def test_health_reports_gateway_and_frontend(self):
        payload = health()

        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["service"], "gateway")
        self.assertEqual(payload["host"], "127.0.0.1")
        self.assertEqual(payload["port"], 6401)
        self.assertEqual(payload["frontend"], "http://127.0.0.1:6400")

    def test_services_returns_registered_services_list(self):
        payload = services()

        self.assertEqual(payload, {"services": []})


if __name__ == "__main__":
    unittest.main()


class DebugEventEndpointTests(unittest.TestCase):
    def test_debug_events_reads_event_logs_from_storage_path(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "aurabot.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                "create table event_logs (id text, event_type text, payload_json text, created_at text)"
            )
            connection.execute(
                "insert into event_logs values (?, ?, ?, ?)",
                (
                    "event-1",
                    "memory.write",
                    '{"profileId":"profile-1"}',
                    "2026-05-21T00:00:00.000Z",
                ),
            )
            connection.commit()
            connection.close()

            response = TestClient(app).get(
                "/debug/events",
                params={"storage_path": str(database_path), "limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "events": [
                    {
                        "id": "event-1",
                        "eventType": "memory.write",
                        "payload": {"profileId": "profile-1"},
                        "createdAt": "2026-05-21T00:00:00.000Z",
                    }
                ]
            },
        )

    def test_debug_events_reports_missing_storage_path(self):
        response = TestClient(app).get(
            "/debug/events",
            params={"storage_path": "/tmp/does-not-exist.sqlite"},
        )

        self.assertEqual(response.status_code, 404)


class ChatMainSessionEndpointTests(unittest.TestCase):
    def test_chat_main_session_reads_main_session_messages_for_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "aurabot.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                create table sessions (
                  id text, profile_id text, kind text, title text, parent_session_id text,
                  depth integer, created_at text, updated_at text
                )
                """
            )
            connection.execute(
                """
                create table messages (
                  id text, session_id text, profile_id text, role text, content text,
                  metadata_json text, created_at text
                )
                """
            )
            connection.execute(
                "insert into sessions values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "session-main",
                    "profile-1",
                    "main",
                    "Main chat",
                    None,
                    0,
                    "2026-05-21T00:00:00.000Z",
                    "2026-05-21T00:00:01.000Z",
                ),
            )
            connection.execute(
                "insert into messages values (?, ?, ?, ?, ?, ?, ?)",
                (
                    "message-1",
                    "session-main",
                    "profile-1",
                    "user",
                    "你好",
                    '{"attachments":[{"id":"attachment-1","kind":"file","purpose":"input","name":"note.md","path":"/tmp/note.md"}]}',
                    "2026-05-21T00:00:02.000Z",
                ),
            )
            connection.commit()
            connection.close()

            response = TestClient(app).get(
                "/chat/main-session",
                params={"storage_path": str(database_path), "profile_id": "profile-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "sessionId": "session-main",
                "messages": [
                    {
                        "id": "message-1",
                        "sessionId": "session-main",
                        "profileId": "profile-1",
                        "role": "user",
                        "content": "你好",
                        "attachments": [
                            {
                                "id": "attachment-1",
                                "kind": "file",
                                "purpose": "input",
                                "name": "note.md",
                                "path": "/tmp/note.md",
                            }
                        ],
                        "createdAt": "2026-05-21T00:00:02.000Z",
                    }
                ],
            },
        )

    def test_chat_main_session_reports_missing_storage_path(self):
        response = TestClient(app).get(
            "/chat/main-session",
            params={"storage_path": "/tmp/does-not-exist.sqlite", "profile_id": "profile-1"},
        )

        self.assertEqual(response.status_code, 404)


class TasksEndpointTests(unittest.TestCase):
    def test_tasks_reads_tasks_for_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "aurabot.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                create table tasks (
                  id text, profile_id text, parent_session_id text, session_id text,
                  task_kind text, title text, status text, latest_progress text,
                  due_at text, created_at text, updated_at text
                )
                """
            )
            connection.execute(
                "insert into tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "task-1",
                    "profile-1",
                    "session-main",
                    "session-task",
                    "time_reminder",
                    "提醒开会",
                    "queued",
                    "Reminder scheduled from main chat.",
                    "2026-05-22T09:00:00.000Z",
                    "2026-05-21T00:00:00.000Z",
                    "2026-05-21T00:00:01.000Z",
                ),
            )
            connection.execute(
                "insert into tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "task-2",
                    "profile-other",
                    "session-main",
                    "session-task-other",
                    "result_output",
                    "其他用户任务",
                    "queued",
                    "Task queued from main chat.",
                    None,
                    "2026-05-21T00:00:02.000Z",
                    "2026-05-21T00:00:03.000Z",
                ),
            )
            connection.commit()
            connection.close()

            response = TestClient(app).get(
                "/tasks",
                params={"storage_path": str(database_path), "profile_id": "profile-1"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "tasks": [
                    {
                        "id": "task-1",
                        "profileId": "profile-1",
                        "parentSessionId": "session-main",
                        "sessionId": "session-task",
                        "kind": "time_reminder",
                        "title": "提醒开会",
                        "status": "queued",
                        "latestProgress": "Reminder scheduled from main chat.",
                        "dueAt": "2026-05-22T09:00:00.000Z",
                        "createdAt": "2026-05-21T00:00:00.000Z",
                        "updatedAt": "2026-05-21T00:00:01.000Z",
                    }
                ],
            },
        )

    def test_tasks_reports_missing_storage_path(self):
        response = TestClient(app).get(
            "/tasks",
            params={"storage_path": "/tmp/does-not-exist.sqlite", "profile_id": "profile-1"},
        )

        self.assertEqual(response.status_code, 404)


class DebugMemoryEndpointTests(unittest.TestCase):
    def test_debug_memory_reads_memory_context_and_recent_memory_events(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "aurabot.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                create table memory_nodes (
                  id text, profile_id text, type text, state text, content text,
                  importance real, starts_at text, ends_at text, created_at text, updated_at text
                )
                """
            )
            connection.execute(
                """
                create table memory_edges (
                  id text, profile_id text, source_node_id text, target_node_id text,
                  relation_type text, weight real, created_at text
                )
                """
            )
            connection.execute(
                "create table memory_summaries (id text, profile_id text, topic text, content text, updated_at text)"
            )
            connection.execute(
                "create table event_logs (id text, event_type text, payload_json text, created_at text)"
            )
            connection.execute(
                "insert into memory_nodes values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "node-1",
                    "profile-1",
                    "preference",
                    "active",
                    "喜欢火锅",
                    0.8,
                    None,
                    None,
                    "2026-05-21T00:00:00.000Z",
                    "2026-05-21T00:00:01.000Z",
                ),
            )
            connection.execute(
                "insert into memory_edges values (?, ?, ?, ?, ?, ?, ?)",
                (
                    "edge-1",
                    "profile-1",
                    "node-1",
                    "node-2",
                    "relates_to",
                    0.7,
                    "2026-05-21T00:00:02.000Z",
                ),
            )
            connection.execute(
                "insert into memory_summaries values (?, ?, ?, ?, ?)",
                (
                    "summary-1",
                    "profile-1",
                    "preference",
                    "喜欢火锅",
                    "2026-05-21T00:00:03.000Z",
                ),
            )
            connection.execute(
                "insert into event_logs values (?, ?, ?, ?)",
                (
                    "event-memory",
                    "memory.write",
                    '{"nodeId":"node-1"}',
                    "2026-05-21T00:00:04.000Z",
                ),
            )
            connection.commit()
            connection.close()

            response = TestClient(app).get(
                "/debug/memory",
                params={"storage_path": str(database_path), "profile_id": "profile-1", "event_limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["nodes"][0]["id"], "node-1")
        self.assertEqual(payload["nodes"][0]["profileId"], "profile-1")
        self.assertEqual(payload["nodes"][0]["startsAt"], None)
        self.assertEqual(payload["edges"][0]["sourceNodeId"], "node-1")
        self.assertEqual(payload["summaries"][0]["topic"], "preference")
        self.assertEqual(payload["recentEvents"][0]["eventType"], "memory.write")
        self.assertIn("generatedAt", payload)

    def test_debug_memory_reports_missing_storage_path(self):
        response = TestClient(app).get(
            "/debug/memory",
            params={"storage_path": "/tmp/does-not-exist.sqlite", "profile_id": "profile-1"},
        )

        self.assertEqual(response.status_code, 404)


class DebugTasksEndpointTests(unittest.TestCase):
    def test_debug_tasks_reads_task_details_for_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "aurabot.sqlite"
            connection = sqlite3.connect(database_path)
            connection.execute(
                """
                create table tasks (
                  id text, profile_id text, parent_session_id text, session_id text,
                  task_kind text, title text, status text, latest_progress text,
                  due_at text, created_at text, updated_at text
                )
                """
            )
            connection.execute(
                "create table task_events (id text, task_id text, event_type text, payload_json text, created_at text)"
            )
            connection.execute(
                "create table task_artifacts (id text, task_id text, artifact_type text, uri text, created_at text)"
            )
            connection.execute(
                """
                create table task_input_artifacts (
                  id text, task_id text, artifact_type text, original_name text,
                  mime_type text, size_bytes integer, uri text, created_at text
                )
                """
            )
            connection.execute(
                "insert into tasks values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "task-1",
                    "profile-1",
                    "session-main",
                    "session-task",
                    "result_output",
                    "整理周报",
                    "completed",
                    "已完成",
                    None,
                    "2026-05-21T00:00:00.000Z",
                    "2026-05-21T00:00:05.000Z",
                ),
            )
            connection.execute(
                "insert into task_events values (?, ?, ?, ?, ?)",
                (
                    "event-1",
                    "task-1",
                    "task.agent.completed",
                    '{"summary":"完成任务","durationMs":5000}',
                    "2026-05-21T00:00:05.000Z",
                ),
            )
            connection.execute(
                "insert into task_artifacts values (?, ?, ?, ?, ?)",
                (
                    "artifact-1",
                    "task-1",
                    "file",
                    "/tmp/report.md",
                    "2026-05-21T00:00:06.000Z",
                ),
            )
            connection.execute(
                "insert into task_input_artifacts values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "input-1",
                    "task-1",
                    "file",
                    "notes.md",
                    "text/markdown",
                    12,
                    "/tmp/notes.md",
                    "2026-05-21T00:00:01.000Z",
                ),
            )
            connection.commit()
            connection.close()

            response = TestClient(app).get(
                "/debug/tasks",
                params={"storage_path": str(database_path), "profile_id": "profile-1", "limit": 10},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tasks"][0]["task"]["id"], "task-1")
        self.assertEqual(payload["tasks"][0]["timeline"][0]["summary"], "完成任务")
        self.assertEqual(payload["tasks"][0]["artifacts"][0]["name"], "report.md")
        self.assertEqual(payload["tasks"][0]["inputArtifacts"][0]["name"], "notes.md")
        self.assertIn("generatedAt", payload)

    def test_debug_tasks_reports_missing_storage_path(self):
        response = TestClient(app).get(
            "/debug/tasks",
            params={"storage_path": "/tmp/does-not-exist.sqlite", "profile_id": "profile-1"},
        )

        self.assertEqual(response.status_code, 404)
