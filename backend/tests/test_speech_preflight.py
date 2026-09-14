"""Verify explicit voice preparation rejects missing assets with actionable, secret-free codes.

Tests exercise the production preflight before any provider or device is opened.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend.main import _prepare_speech

class SpeechPreflightTests(unittest.TestCase):
    def test_reports_each_missing_asset(self):
        with tempfile.TemporaryDirectory() as root:
            paths = {name: str(Path(root) / name) for name in ('silero_model_path', 'silero_license_path', 'smart_turn_model_path', 'smart_turn_license_path')}
            settings = SimpleNamespace(api_key='secret', **paths)
            for field, code in zip(paths, ('speech_vad_model_asset_missing','speech_vad_license_asset_missing','speech_eou_model_asset_missing','speech_eou_license_asset_missing')):
                with self.assertRaisesRegex(RuntimeError, '^'+code+'$'):
                    _prepare_speech(settings)
                Path(paths[field]).write_text('MIT License BSD 2-Clause')

    def test_empty_key_is_distinct(self):
        with self.assertRaisesRegex(RuntimeError, '^speech_api_key_missing$'):
            _prepare_speech(SimpleNamespace(api_key=''))

    def test_invalid_license_and_missing_dependency(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'asset'
            path.write_text('invalid')
            settings = SimpleNamespace(api_key='secret', silero_model_path=str(path), silero_license_path=str(path), smart_turn_model_path=str(path), smart_turn_license_path=str(path))
            with self.assertRaisesRegex(RuntimeError, '^speech_vad_license_unverified$'):
                _prepare_speech(settings)
            path.write_text('MIT License BSD 2-Clause')
            with patch('backend.main.importlib.util.find_spec', side_effect=ModuleNotFoundError('private detail')):
                with self.assertRaisesRegex(RuntimeError, '^speech_optional_dependency_missing$'):
                    _prepare_speech(settings)

    def test_incompatible_model_fails_before_voice_is_accepted(self):
        detector = SimpleNamespace(status=lambda: SimpleNamespace(reason='ready_to_load'), speech_probability=lambda _: None)
        with patch('backend.main.SileroVadDetector', return_value=detector), patch('backend.main.SmartTurnDetector') as eou, patch('backend.main.importlib.util.find_spec', return_value=object()):
            eou.return_value.status.return_value.reason = 'ready_to_load'
            settings = SimpleNamespace(api_key='secret',silero_model_path='',silero_license_path='',smart_turn_model_path='',smart_turn_license_path='')
            # A failed inference carries the detector's reason, not a provider exception.
            calls = iter(['ready_to_load','inference_failed'])
            detector.status = lambda: SimpleNamespace(reason=next(calls))
            with self.assertRaisesRegex(RuntimeError, '^speech_vad_inference_failed$'):
                _prepare_speech(settings)
