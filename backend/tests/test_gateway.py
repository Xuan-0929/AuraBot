"""
验证 health-only Backend 的公开 HTTP 边界。

测试通过真实 FastAPI TestClient 检查配置初始化、固定健康响应和旧 Task/Plan
路径缺席；它不创建任何 Task runtime，也不模拟已退役的应用服务。
"""

import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault(
    "AURABOT_CONFIG_PATH",
    str(Path(__file__).parent / "fixtures" / "runtime_config.yaml"),
)

from backend.config import config as global_config
from backend.main import create_app


class GatewayEndpointTests(unittest.TestCase):
    def test_health_is_runtime_independent_and_exact(self):
        application = create_app()
        with TestClient(application) as client:
            response = client.get("/health")

        self.assertTrue(global_config.initialized)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "service": "backend"})
        self.assertIsNone(application.state.main_agent_profiles.active_profile_id)

    def test_removed_execution_routes_are_not_registered(self):
        with TestClient(create_app()) as client:
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
