"""Exercise voice configuration migration and provider isolation at the backend boundary.

These tests use the real settings parser: existing installations keep their pipeline,
while Realtime can be selected without supplying local inference assets or Pipeline credentials.
Invalid modes and provider identities must fail before opening a network connection. Legacy enabled=false cannot block a configured microphone session.
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
            "enabled": False, "mode": "realtime", "realtime": {"api_key": "key", "voice": "voice-id"},
        }}}, Path("/tmp/config.json"))
        with patch("backend.main.SileroVadDetector", side_effect=AssertionError("must not load VAD")):
            self.assertTrue(voice_available(settings))

    def test_legacy_vendor_migration_clears_only_pipeline_credentials(self):
        settings = SpeechSettings.from_config({
            "mode": "realtime", "api_key": "old-provider-secret",
            "stt_model_name": "fun-asr-realtime", "tts_model_name": "cosyvoice-v3-flash",
            "tts_voice": "longanyang", "realtime": {"api_key": "realtime-key", "voice": "voice-id"},
        }, config_root=Path("/tmp"))
        self.assertEqual(settings.api_key, "")
        self.assertEqual(settings.stt_model_name, "volc.seedasr.sauc.duration")
        self.assertEqual(settings.tts_model_name, "seed-tts-2.0")
        self.assertEqual(settings.tts_voice, "zh_female_vv_uranus_bigtts")
        self.assertEqual(settings.realtime.api_key, "realtime-key")
        self.assertEqual(settings.mode, "realtime")
