"""对项目 pinned Pi 0.84.2 执行不访问模型的离线 RPC smoke。

测试使用临时 Pi Home/session 加载真实 AuraBot Extension，只发送 get_state 后关闭 stdin；
stderr 独立用 PIPE 捕获启动诊断，不复用生产 DEVNULL 或用户 ``~/.pi/agent``。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
NODE = os.environ.get("AURABOT_NODE_EXECUTABLE", "node")
PI_CLI = PROJECT_ROOT / "pi-runtime/node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
EXTENSION = PROJECT_ROOT / "pi-aurabot-task/aurabot-task/index.ts"
SYSTEM_PROMPT = PROJECT_ROOT / "pi-aurabot-task/prompts/task-system.md"


class RealPiOfflineSmokeTests(unittest.IsolatedAsyncioTestCase):
    async def test_pinned_pi_loads_extension_and_answers_get_state_offline(self):
        version = subprocess.run(
            [NODE, str(PI_CLI), "--version"], check=True, capture_output=True,
            text=True, timeout=15,
        )
        self.assertEqual(version.stdout.strip(), "0.84.2")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session_dir, pi_home = root / "sessions", root / "pi-agent"
            session_dir.mkdir()
            pi_home.mkdir()
            environment = os.environ.copy()
            environment.update({
                "PI_CODING_AGENT_DIR": str(pi_home), "PI_OFFLINE": "1",
                "PI_SKIP_VERSION_CHECK": "1", "PI_TELEMETRY": "0",
            })
            process = await asyncio.create_subprocess_exec(
                NODE, str(PI_CLI), "--mode", "rpc", "--session-dir", str(session_dir),
                "--session-id", "00000000-0000-4000-8000-000000000001",
                "--name", "offline-smoke", "--no-approve", "--extension", str(EXTENSION),
                "--append-system-prompt", str(SYSTEM_PROMPT), cwd=root, env=environment,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True,
            )
            assert process.stdin and process.stdout and process.stderr
            process.stdin.write(b'{"id":"smoke-1","type":"get_state"}\n')
            await process.stdin.drain()
            response = None
            async with asyncio.timeout(15):
                while line := await process.stdout.readline():
                    value = json.loads(line)
                    if value.get("type") == "response" and value.get("id") == "smoke-1":
                        response = value
                        break
            self.assertIsNotNone(response)
            self.assertTrue(response["success"])
            self.assertEqual(
                response["data"]["sessionId"],
                "00000000-0000-4000-8000-000000000001",
            )
            process.stdin.close()
            await process.stdin.wait_closed()
            returncode = await asyncio.wait_for(process.wait(), timeout=15)
            stderr = (await process.stderr.read()).decode(errors="replace")
            self.assertEqual(returncode, 0, stderr)
            self.assertNotIn("Extension load failed", stderr)
