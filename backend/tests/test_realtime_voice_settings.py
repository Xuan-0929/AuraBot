"""Exercise voice configuration migration and provider isolation at the backend boundary.

These tests use the real settings parser: existing installations keep their pipeline,
while Realtime can be selected without supplying local inference assets or DashScope credentials.
Invalid modes and provider identities must fail before opening a network connection.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main_agent.settings import SpeechSettings


class RealtimeVoiceSettingsTests(unittest.TestCase):
    def test_existing_configuration_keeps_pipeline(self):
        settings = SpeechSettings.from_config({"enabled": True, "api_key": "pipeline-key"}, config_root=Path("/tmp"))
        self.assertEqual(settings.mode, "pipeline")
        self.assertEqual(settings.api_key, "pipeline-key")

    def test_realtime_uses_separate_credentials_without_pipeline_assets(self):
        settings = SpeechSettings.from_config({
            "enabled": True, "mode": "realtime",
            "realtime": {"provider": "seeduplex", "api_key": " rt-key ", "voice": "voice-id"},
        }, config_root=Path("/tmp"))
        self.assertEqual(settings.mode, "realtime")
        self.assertEqual(settings.api_key, "")
        self.assertEqual(settings.realtime.api_key, "rt-key")
        self.assertEqual(settings.realtime.voice, "voice-id")

    def test_rejects_unsupported_mode_and_provider(self):
        for raw in ({"mode": "other"}, {"realtime": {"provider": "other"}}, {"realtime": []}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                SpeechSettings.from_config(raw, config_root=Path("/tmp"))

    def test_realtime_readiness_does_not_probe_pipeline_models(self):
        from backend.main import voice_available
        from backend.main_agent.settings import MainAgentSettings
        settings = MainAgentSettings.from_config({"main_agent": {"speech": {
            "enabled": True, "mode": "realtime", "realtime": {"api_key": "key", "voice": "voice-id"},
        }}}, Path("/tmp/config.json"))
        with patch("backend.main.SileroVadDetector", side_effect=AssertionError("must not load VAD")):
            self.assertTrue(voice_available(settings))
