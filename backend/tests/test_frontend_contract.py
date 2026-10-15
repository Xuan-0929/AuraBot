"""Exercise desktop realtime delivery, restart admission and owner-scoped Task management.

Controlled speech failures preserve text sessions and redact unknown errors. Real Runtime/Context/TaskStore owners run against temporary databases. Controlled model output proves
that desktop history receives accepted inputs and say_to_user only, while restart fences all admissions.
Speech lifecycle fixtures use near-field PCM and an explicit short confirmation window; production
acoustic thresholds are exercised separately by the speech coordinator tests.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.gateway.readiness_routes import prepare_restart, RestartPrepareRequest
from backend.main_agent.context import ContextStore, ClauseState
from backend.main_agent.realtime import RealtimeHub
from backend.main_agent.lifecycle import ProfileResources
from backend.main_agent.runtime import MainAgentRuntime
from backend.main_agent.snapshot import SnapshotProjector
from backend.main_agent.tasks import TaskBridge
from backend.main_agent.tool_runtime import ToolCall, ToolSurfacePolicy
from backend.task.manager import TaskManager
from backend.task.models import CreateTaskCommand, TaskRuntimeUnavailable, TaskStatus
from backend.task.settings import TaskSettings
from backend.tests.test_main_agent_realtime import FakeSocket, ORIGIN
from backend.tests.test_main_agent_tasks import delta


class DesktopContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.context = ContextStore(self.root / 'context.sqlite3')
        self.hub = RealtimeHub(allowed_origins={ORIGIN})

        async def provider(request):
            yield {'choices': [{'delta': {'content': 'private reasoning'}}]}
            yield delta('say', 'say_to_user', '{"text":"Hello"}')

        self.runtime = MainAgentRuntime(context_store=self.context,
            snapshot_projector=SnapshotProjector(), tool_policy=ToolSurfacePolicy(),
            provider=provider, renderer=self.hub.render, executors={}, soul='Soul')
        await self.runtime.startup()
        self.profile = ProfileResources('profile', self.runtime, None, None)
        await self.hub.set_active('profile', 1, self.profile)
        self.socket = FakeSocket()
        self.session = await self.hub.attach(self.socket)
        self.manager = TaskManager(TaskSettings.from_mapping(
            {'app': {'workspaces': [{'id': 'repo', 'path': str(self.root)}]}, 'task': {}},
            config_root=self.root))
        self.manager.capability_available = False
        await self.manager.startup()
        self.request = SimpleNamespace(headers={'origin': ORIGIN}, app=SimpleNamespace(
            state=SimpleNamespace(main_agent_realtime=self.hub, task_manager=self.manager, backend_ready=True)))

    async def asyncTearDown(self):
        await self.hub.stop_accepting()
        await self.runtime.shutdown()
        await self.manager.shutdown()
        self.context.close()
        self.temp.cleanup()

    async def wait_offer(self, *, after=None):
        for _ in range(100):
            offers = [event for event in self.socket.sent if isinstance(event, dict) and event.get('type') == 'clause.offer']
            if offers and offers[-1]['clause_id'] != after:
                return offers[-1]
            await asyncio.sleep(.001)
        self.fail('missing clause.offer')

    async def test_text_history_is_accepted_input_and_say_only_and_ack_commits_draft(self):
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 'message-1', 'text': 'Hi'})
        offer = await self.wait_offer()
        self.assertEqual(offer['mode'], 'text')
        self.assertEqual(self.context.get_clause(offer['clause_id']).state, ClauseState.DRAFT)
        user = next(event for event in self.socket.sent if event.get('type') == 'user.message')
        self.assertEqual(user, {'type': 'user.message', 'message_id': 'message-1', 'text': 'Hi', 'source': 'text', 'profile_id': 'profile', 'generation': 1})
        self.assertLess(self.socket.sent.index(user), next(i for i,e in enumerate(self.socket.sent) if e.get('type') == 'command.ack'))
        self.assertNotIn('private reasoning', str(self.socket.sent))
        self.assertTrue(await self.hub.renderer_ack(self.session, offer['clause_id'], complete=True))
        await self.runtime.wait_until_idle()
        self.assertEqual(self.context.get_clause(offer['clause_id']).state, ClauseState.DELIVERED)
        self.assertEqual(self.context.fold().conversation_turns[-1]['content'], 'Hello')

    async def test_partial_voice_transcript_stays_private_until_endpoint(self):
        await self.runtime.update_transcript('partial', segment_id='voice-1', request_prepare=False)
        await self.runtime.update_transcript('final words', segment_id='voice-1', request_prepare=False)
        self.assertFalse(any(e.get('type') == 'user.message' for e in self.socket.sent))
        await self.runtime.eou_complete()
        user = next(e for e in self.socket.sent if e.get('type') == 'user.message')
        self.assertEqual((user['message_id'], user['text'], user['source']), ('voice-1', 'final words', 'voice'))

    async def test_missing_voice_is_explicit_rejection_and_text_session_survives(self):
        await self.hub.command(self.session, {'type': 'voice.start', 'command_id': 'v1'})
        self.assertEqual(self.socket.sent[-1], {'type': 'command.ack', 'command_id': 'v1', 'status': 'rejected', 'code': 'speech_unavailable'})
        self.assertEqual(self.session.mode, 'text')
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 't1', 'text': 'Hi'})
        await self.wait_offer()

    async def test_restart_busy_has_no_effect_and_force_fences_input_and_dispatch(self):
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 't1', 'text': 'Hi'})
        await self.wait_offer()
        queued = self.manager._store.create_task(CreateTaskCommand('T', 'P', 'repo', 'profile', 'key', 60), self.root)
        before = self.manager._store.get_task(queued.id)
        rejected = await prepare_restart(RestartPrepareRequest(force=False), self.request)
        self.assertFalse(rejected['ready'])
        self.assertEqual(rejected['busy'], {'tasks': 1, 'mainAgent': True, 'memoryWriting': False})
        self.assertTrue(self.hub.accepting and self.runtime.accepting and self.manager.accepting)
        accepted = await prepare_restart(RestartPrepareRequest(force=True), self.request)
        self.assertTrue(accepted['ready'])
        self.assertFalse(self.hub.accepting or self.runtime.accepting or self.manager.accepting)
        with self.assertRaisesRegex(RuntimeError, 'backend_restarting'):
            await self.runtime.submit_text('after fence')
        with self.assertRaises(TaskRuntimeUnavailable):
            await self.manager.create_task(CreateTaskCommand('T', 'P', 'repo', 'profile', 'after'))
        self.manager.capability_available = True
        self.manager._wake.set()
        await asyncio.sleep(.005)
        self.assertEqual(self.manager._store.get_task(queued.id), before)

    async def test_idle_memory_timer_is_not_writing_and_is_fenced(self):
        timer = asyncio.create_task(asyncio.sleep(100))
        self.runtime._memory_idle_task = timer
        ready = await prepare_restart(RestartPrepareRequest(), self.request)
        self.assertTrue(ready['ready'])
        self.assertFalse(ready['busy']['memoryWriting'])
        await asyncio.gather(timer, return_exceptions=True)
        self.assertTrue(timer.cancelled())

    async def _disconnect_voice(self, transition: str):
        from unittest.mock import AsyncMock
        from backend.main_agent.speech import SpeechInputCoordinator, SpeechService, TranscriptUpdate
        from backend.tests.test_main_agent_speech import FakeStt, FakeVad, NEAR_PCM
        stt = FakeStt()
        coordinator = SpeechInputCoordinator(self.runtime, stt_provider=stt,
            vad=FakeVad([1.0]), playback_lifecycle=self.hub, speech_confirm_ms=20)
        delivery = SimpleNamespace(startup=AsyncMock(), shutdown=AsyncMock(), cancel_current=AsyncMock())
        speech = SpeechService(self.hub, coordinator, delivery)
        self.profile.speech = speech
        self.session.mode = 'voice'
        await speech.startup()
        await coordinator.accept_mic_frame(NEAR_PCM)
        stt.sessions[0].close = AsyncMock()
        await stt.sessions[0].sink(TranscriptUpdate('unfinished', '', 'unfinished', 'partial'))
        await self.runtime.wait_until_idle()
        self.assertTrue(self.runtime._confirmed_speech)
        old_session = self.session
        if transition == 'detach':
            await self.hub.detach(old_session)
        elif transition == 'refresh':
            await self.hub.set_active('profile', 2, self.profile)
        new_socket = FakeSocket()
        new_session = await self.hub.attach(new_socket)
        self.assertTrue(old_session.closed)
        self.assertIsNone(self.profile.speech)
        self.assertIsNone(self.hub.speech)
        self.assertTrue(coordinator._closed)
        self.assertFalse(coordinator.audio or coordinator.pre_roll)
        stt.sessions[0].close.assert_awaited_once()
        self.assertFalse(self.runtime._user_speaking or self.runtime._speech_candidate or self.runtime._confirmed_speech)
        old_context = self.context.fold()
        await stt.sessions[0].sink(TranscriptUpdate('late forbidden', '', 'late forbidden', 'partial'))
        self.assertEqual(self.context.fold(), old_context)
        await self.hub.command(new_session, {'type': 'text.submit', 'command_id': 'after-disconnect', 'text': 'Fresh text'})
        users = [event for event in new_socket.sent if event.get('type') == 'user.message']
        self.assertEqual([event['text'] for event in users], ['Fresh text'])
        self.assertEqual(new_session.mode, 'text')

    async def test_disconnect_closes_candidate_before_text_reconnect(self):
        await self._disconnect_voice('detach')

    async def test_replacement_closes_candidate_before_new_session(self):
        await self._disconnect_voice('replace')

    async def test_profile_refresh_does_not_reinstall_closed_speech(self):
        await self._disconnect_voice('refresh')

    async def _endpoint_final_transcript(self, *, partial: bool):
        from backend.main_agent.speech import SpeechInputCoordinator, VolcengineStreamingSttProvider
        from backend.tests.test_main_agent_speech import FakeVolcengineSttTransport, FakeVad, NEAR_PCM
        transports = []
        class FinalTransport(FakeVolcengineSttTransport):
            def stop(self):
                self.on_event({'result': {'text': 'complete final words'}, 'final': True})
                self.stopped = True
        def factory(**kwargs):
            transport = FinalTransport(kwargs['on_event'])
            transports.append(transport)
            return transport
        coordinator = SpeechInputCoordinator(self.runtime,
            stt_provider=VolcengineStreamingSttProvider('test', transport_factory=factory),
            vad=FakeVad([1.0, 0.0] if partial else [1.0, 1.0, 0.0]), vad_stop_ms=20, speech_confirm_ms=20,
            eou=SimpleNamespace(completion_probability=lambda _audio: 1.0),
            playback_lifecycle=self.hub)
        try:
            await coordinator.accept_mic_frame(NEAR_PCM)
            if partial:
                transports[0].emit_partial("partial words")
                for _ in range(20):
                    if self.runtime.active_input is not None:
                        break
                    await asyncio.sleep(0)
            else:
                await coordinator.accept_mic_frame(NEAR_PCM)
            await coordinator.accept_mic_frame(NEAR_PCM)
            await coordinator.wait_for_endpoint()
            users = [event for event in self.socket.sent if event.get('type') == 'user.message']
            self.assertEqual([event['text'] for event in users], ['complete final words'])
            self.assertTrue(transports[0].stopped)
        finally:
            await coordinator.close()

    async def test_endpoint_waits_for_final_transcript_emitted_by_transport_stop(self):
        await self._endpoint_final_transcript(partial=True)

    async def test_endpoint_accepts_final_only_transcript_without_any_partial(self):
        await self._endpoint_final_transcript(partial=False)

    async def test_typed_input_during_voice_keeps_spoken_delivery_and_retires_old_mic_turn(self):
        from backend.main_agent.speech import SpeechInputCoordinator, SpeechService, ClauseSpeechDelivery, TranscriptUpdate
        from backend.tests.test_main_agent_speech import FakeStt, FakeVad, NEAR_PCM
        class Output:
            def __init__(self):
                self.started, self.cancelled = asyncio.Event(), asyncio.Event()
            async def synthesize(self, _text):
                yield b"\0\0" * 480
                if not self.started.is_set():
                    self.started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        self.cancelled.set()
        stt = FakeStt()
        coordinator = SpeechInputCoordinator(self.runtime, stt_provider=stt,
            vad=FakeVad([1.0, 1.0]), playback_lifecycle=self.hub, speech_confirm_ms=20)
        output = Output()
        delivery = ClauseSpeechDelivery(output, self.hub.acquire_renderer_port, self.runtime)
        speech = SpeechService(self.hub, coordinator, delivery)
        self.profile.speech = speech
        self.session.mode = 'voice'
        await speech.startup()
        mic_worker, delivery_worker = speech._mic_task, delivery._worker
        await coordinator.accept_mic_frame(NEAR_PCM)
        old_sink = stt.sessions[0].sink
        await old_sink(TranscriptUpdate('unfinished mic', '', 'unfinished mic', 'partial'))
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 'typed', 'text': 'Typed while talking'})
        offer = await self.wait_offer()
        self.assertEqual(offer['mode'], 'voice')
        self.assertEqual(self.session.mode, 'voice')
        self.assertIs(self.profile.speech, speech)
        self.assertIs(speech._mic_task, mic_worker)
        self.assertIs(delivery._worker, delivery_worker)
        self.assertFalse(mic_worker.done() or delivery_worker.done())
        old_context = self.context.fold()
        await old_sink(TranscriptUpdate('late forbidden', '', 'late forbidden', 'partial'))
        self.assertEqual(self.context.fold(), old_context)
        users = [event for event in self.socket.sent if isinstance(event, dict) and event.get('type') == 'user.message']
        self.assertEqual([(event['text'], event['source']) for event in users], [('Typed while talking', 'text')])
        ack = next(event for event in self.socket.sent if isinstance(event, dict) and event.get('type') == 'command.ack')
        self.assertEqual(ack['status'], 'accepted')
        await output.started.wait()
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 'typed-again', 'text': 'Replace the slow reply'})
        await asyncio.wait_for(output.cancelled.wait(), .3)
        next_offer = await self.wait_offer(after=offer['clause_id'])
        self.assertEqual(next_offer['mode'], 'voice')
        self.assertIs(delivery._worker, delivery_worker)

    async def test_unconfigured_main_model_rejects_text_before_history(self):
        self.runtime.text_available = False
        await self.hub.command(self.session, {'type': 'text.submit', 'command_id': 'blank', 'text': 'Hi'})
        self.assertEqual(self.socket.sent[-1]['code'], 'text_model_unavailable')
        self.assertEqual(self.context.fold().conversation_turns, ())
        self.assertFalse(any(e.get('type') == 'user.message' for e in self.socket.sent))

    async def test_actual_memory_archive_blocks_nonforced_restart(self):
        writing = asyncio.create_task(asyncio.sleep(100))
        self.runtime._memory_archive_task = writing
        result = await prepare_restart(RestartPrepareRequest(), self.request)
        self.assertFalse(result['ready'])
        self.assertTrue(result['busy']['memoryWriting'])
        self.assertTrue(self.runtime.accepting)
        writing.cancel()
        await asyncio.gather(writing, return_exceptions=True)

    async def test_blank_profile_composition_never_prepares_speech(self):
        from unittest.mock import patch
        os.environ.setdefault("AURABOT_CONFIG_PATH", str(Path(__file__).parent / "fixtures/runtime_config.json"))
        from backend.main import _build_profile
        from backend.main_agent.metrics import LifecycleMetrics
        from backend.main_agent.settings import MainAgentSettings
        settings = MainAgentSettings.from_config({}, self.root / 'config.json')
        hub = RealtimeHub()
        with patch('backend.main._prepare_speech', side_effect=AssertionError('speech must stay lazy')):
            resources = await _build_profile(profile_id='blank', path=self.root / 'blank', soul='Soul',
                manager=self.manager, hub=hub, settings=settings, metrics=LifecycleMetrics(),
                provider_factory=None, speech_factory=None)
        self.assertIsNone(resources.speech)
        await resources.shutdown()

    async def test_owner_filter_delete_and_rerun_use_normal_task_state(self):
        own = self.manager._store.create_task(CreateTaskCommand('T', 'private prompt', 'repo', 'profile', 'one', 60), self.root)
        self.manager._store.create_task(CreateTaskCommand('Foreign', 'P', 'repo', 'other', 'two', 60), self.root)
        cancelled, _ = self.manager._store.transition_task(own.id, TaskStatus.CANCELLED, expected_version=own.version)
        page = await self.manager.list_tasks(owner_profile_id='profile')
        self.assertEqual([t.id for t in page.items], [own.id])
        bridge = TaskBridge(self.manager, 'profile')
        self.manager.capability_available = True
        result = await bridge.rerun_task(ToolCall('rerun', 'rerun_task', {'task_id': str(own.id)}))
        rerun = await self.manager.get_task(result['task_id'])
        self.assertEqual((rerun.prompt, rerun.rerun_of_task_id, rerun.owner_profile_id), ('private prompt', own.id, 'profile'))
        self.manager.capability_available = False
        deleted = await bridge.delete_task(ToolCall('delete', 'delete_task', {'task_id': str(own.id)}))
        self.assertTrue(deleted['deleted'])
        self.assertEqual(deleted['version'], cancelled.version + 1)
        self.assertNotIn('private prompt', str(deleted))

    async def test_known_voice_failure_keeps_text_session(self):
        def fail():
            raise RuntimeError('speech_vad_model_asset_missing')
        self.profile.speech_factory = fail
        await self.hub.command(self.session, {'type':'voice.start','command_id':'voice-failure'})
        self.assertEqual(self.socket.sent[-1]['code'], 'speech_vad_model_asset_missing')
        self.assertEqual(self.session.mode, 'text')
        self.assertIsNone(self.profile.speech)
        await self.hub.command(self.session, {'type':'text.submit','command_id':'after-voice','text':'hello'})
        self.assertTrue(any(e.get('command_id')=='after-voice' and e.get('status')=='accepted' for e in self.socket.sent))

    async def test_unknown_voice_failure_does_not_expose_details(self):
        def fail():
            raise RuntimeError('sensitive provider detail')
        self.profile.speech_factory = fail
        await self.hub.command(self.session, {'type':'voice.start','command_id':'voice-failure'})
        self.assertEqual(self.socket.sent[-1]['code'], 'speech_unavailable')
