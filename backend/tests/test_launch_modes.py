"""Exercise immutable launch options and HTTP debug permissions through the actual Backend lifespan.

Task/Profile owners are inert here; file logging and readiness remain the production implementations.
Locked-memory integration lives in test_locked_memory and uses actual SQLite/Qdrant stores.
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('AURABOT_CONFIG_PATH', str(Path(__file__).parent / 'fixtures/runtime_config.json'))
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.launch_options import LaunchOptions
from backend.tests.test_main_agent_gateway import Owner, Profiles
from backend.main_agent.realtime import RealtimeHub


class LaunchModeTests(unittest.TestCase):
    def test_locked_environment_requires_existing_absolute_session_directory(self):
        for value in ('', 'relative', '/definitely-missing-aurabot-session'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                LaunchOptions.from_environment({'AURABOT_LOCK_MEMORY': '1', 'AURABOT_SESSION_DATA_DIR': value})

    def test_readiness_and_debug_endpoints_follow_launch_mode(self):
        for debug in (False, True):
            for locked in (False, True):
                with self.subTest(debug=debug, locked=locked), tempfile.TemporaryDirectory() as directory:
                    options=LaunchOptions(debug=debug, lock_memory=locked, session_data_dir=Path(directory) if locked else None)
                    hub=RealtimeHub()
                    app=create_app(task_manager=Owner('task', []), realtime_hub=hub,
                        profile_manager=Profiles(hub, []), launch_options=options)
                    with TestClient(app) as client:
                        ready=client.get('/api/v1/readiness').json()
                        self.assertEqual(ready['launchOptions'], {'debug':debug, 'lockMemory':locked})
                        self.assertEqual(ready['memoryFixture'], 'benchmark-memory-v1' if locked else None)
                        headers={'origin':'http://localhost:6400'}
                        for action, payload in [('configure', {'enabled':True, 'captureId':'test'}), ('clear', {'captureId':'next'})]:
                            response=client.post('/api/v1/main-agent/debug/'+action, headers=headers, json=payload)
                            self.assertEqual(response.status_code, 200 if debug else 403)
                        self.assertEqual(client.get('/api/v1/main-agent/debug/events', headers=headers).status_code, 200 if debug else 403)
                        self.assertEqual(hub.debug.enabled, debug)

    def test_normal_lifespan_does_not_install_speech_file_logging(self):
        for enabled in (False, True):
            hub=RealtimeHub()
            with patch('backend.main.build_task_manager', return_value=Owner('task', [])), patch('backend.main.speech_diagnostics') as diagnostics:
                app=create_app(realtime_hub=hub, profile_manager=Profiles(hub, []), launch_options=LaunchOptions(debug=enabled))
                with TestClient(app):
                    self.assertEqual(diagnostics.call_args.args[0] is not None, enabled)
