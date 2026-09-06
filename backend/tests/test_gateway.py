"""验证 Backend 的固定健康响应与已移除 HTTP surface 不会重新注册。

测试通过真实 FastAPI TestClient 检查配置初始化，并确认旧 Task/Plan 路径以及被删除的
Main-Agent Memory browse/forget/retry 管理面均不出现在 OpenAPI；不创建任何 Profile Runtime。Task 数据使用每个测试独立临时目录，避免旧版本测试库影响结果。
"""

import os
import unittest
import tempfile
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.json"),
)

from backend.config import config as global_config
from backend.main import create_app
from backend.task.manager import TaskManager
from backend.task.settings import TaskSettings


class GatewayEndpointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="aurabot-gateway-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        settings = TaskSettings.from_config(global_config, global_config.path)
        self.manager = TaskManager(replace(settings, database_path=root / "tasks.sqlite3",
            session_root=root / "sessions", pi_home=root / "pi-agent"))
        self.manager.capability_available = False

    def test_health_is_runtime_independent_and_exact(self):
        application = create_app(task_manager=self.manager)
        with TestClient(application) as client:
            response = client.get("/health")

        self.assertTrue(global_config.initialized)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "service": "backend"})
        self.assertIsNone(application.state.main_agent_profiles.active_profile_id)

    def test_removed_execution_routes_are_not_registered(self):
        with TestClient(create_app(task_manager=self.manager)) as client:
            for path in (
                "/task",
                "/task/events",
                "/tool/plan",
                "/tool/plan/current",
                "/services",
                "/tasks",
                "/chat/main-session",
                "/debug/events",
            ):
                self.assertEqual(client.get(path).status_code, 404, path)
            paths = client.get("/openapi.json").json()["paths"]
            self.assertNotIn("/api/v1/profiles/{profile_id}/memories", paths)
            self.assertNotIn("/api/v1/profiles/{profile_id}/memories/forget", paths)
            self.assertNotIn("/api/v1/profiles/{profile_id}/memory-jobs/{job_id}/retry", paths)
