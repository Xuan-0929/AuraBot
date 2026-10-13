"""Exercise Profile capability compilation, partial visual degradation and streaming SAY ownership.

The tests use real policy/parser/harness implementations and deterministic provider chunks; fixtures never
open user storage or external providers. Lifecycle tests separately cover the transport and storage owners.
"""
from __future__ import annotations

import asyncio
import json
import unittest

from backend.main_agent.avatar import AvatarCapabilities
from backend.main_agent.tool_runtime import EffectFence, InvokeMode, ResponseGrant, ToolCall, ToolHarness, ToolSurfacePolicy
from backend.main_agent.tool_runtime.stream import SayTextStream


def adaptor():
    return {"schema_version": 1, "revision": 7, "model": {"manifest": "model/Test.model3.json"}, "entries": {
        "nod": {"kind": "motion", "source": {"file": "model/nod.motion3.json"}, "description": "确认", "agent_enabled": True, "playback": {"mode": "once"}},
        "smile": {"kind": "expression", "source": {"file": "model/smile.exp3.json"}, "description": "友好", "agent_enabled": True, "playback": {"mode": "hold"}},
    }, "validated_resources": ["model/nod.motion3.json", "model/smile.exp3.json"], "states": {}}


class AvatarTests(unittest.TestCase):
    def test_capabilities_and_local_degradation_share_snapshot(self):
        caps = AvatarCapabilities.compile(adaptor(), profile_id="a", revision=7)
        policy = ToolSurfacePolicy(avatar_capabilities=caps)
        grant = ResponseGrant("g", "i", 1, 1)
        surface = policy.freeze_surface(InvokeMode.RESPOND, input_id="i", revision=1, context_version=1, response_grant=grant, current_response_grant=grant)
        schema = surface.definition("say_to_user").parameters
        self.assertEqual(schema["properties"]["avatar"]["properties"]["motion"]["enum"], ["none", "nod"])
        self.assertIsNone(policy.validate_arguments(ToolCall("c", "say_to_user", {"text": "Hello", "avatar": {"motion": "smile"}}), surface))
        self.assertEqual(caps.validate({"motion": "smile", "expression": "smile"}), {"expression": "smile"})
        self.assertEqual(caps.validate(["nod"]), {})
        self.assertEqual(AvatarCapabilities.compile({**adaptor(), "validated_resources": []}).motion_names, ())

    def test_every_character_boundary_both_field_orders_and_nested_names(self):
        for value in ({"avatar": {"motion": "nod"}, "text": "你好。尾巴😀"}, {"text": "你好。尾巴😀", "avatar": {"motion": "nod"}}):
            raw = json.dumps(value, ensure_ascii=True)
            for cut in range(len(raw) + 1):
                stream = SayTextStream()
                self.assertEqual(stream.feed(raw[:cut]) + stream.feed(raw[cut:]), ("你好。",))
                self.assertEqual(stream.remaining_text, "尾巴😀")
                self.assertEqual(stream.avatar, {"motion": "nod"})
                self.assertTrue(stream.object_closed)
        stream = SayTextStream()
        self.assertEqual(stream.feed('{"avatar":{"text":"fake。"},"text":"real。"}'), ("real。",))
        self.assertTrue(SayTextStream().feed('{"text":"x。", "text":"y"}') == ())


class AvatarHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_frozen_avatar_and_closed_before_unrelated_provider_tail(self):
        policy = ToolSurfacePolicy(avatar_capabilities=AvatarCapabilities.compile(adaptor(), profile_id="a", revision=7))
        grant = ResponseGrant("g", "i", 1, 1)
        fence = EffectFence("invoke", "i", 1, 1, grant)
        calls, endings = [], []
        async def execute(call):
            calls.append(call)
        async def close(value):
            endings.append(value)
        harness = ToolHarness(policy, {"say_to_user": execute}, state_provider=lambda: fence, say_closed_hook=close)
        surface = harness.freeze_surface(InvokeMode.RESPOND, input_id="i", revision=1, context_version=1, response_grant=grant, current_response_grant=grant)
        async def provider():
            for fragment in ('{"text":"早句。', '尾句", "avatar":{"motion":"nod"}}'):
                yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "say", "function": {"name": "say_to_user", "arguments": fragment}}]}}]}
            self.assertEqual(len(endings), 1)
            self.assertEqual(len(calls), 2)
            yield {"choices": [{"delta": {"content": "discard"}}]}
        result = await harness.run(provider(), invoke_id="invoke", tool_surface=surface)
        self.assertEqual([call.arguments["text"] for call in calls], ["早句。", "尾句"])
        self.assertTrue(all(call.avatar == {} for call in calls))
        self.assertEqual(endings[0]["clause_count"], 2)
        self.assertFalse(result.cancelled)


class MemoryMigrationTests(unittest.TestCase):
    def test_migration_includes_wal_preserves_originals_and_is_idempotent(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from backend.main_agent.profile_storage import prepare_memory_directory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "memory.sqlite3") as original:
                original.execute("PRAGMA journal_mode=WAL")
                original.execute("CREATE TABLE records(value TEXT)")
                original.execute("INSERT INTO records VALUES ('private A')")
                original.commit()
                (root / "qdrant").mkdir()
                (root / "qdrant" / "index").write_bytes(b"owned vector data")
                target = prepare_memory_directory(root)
                with sqlite3.connect(target / "memory.sqlite3") as migrated:
                    self.assertEqual(migrated.execute("SELECT value FROM records").fetchone(), ("private A",))
                self.assertEqual((target / "qdrant" / "index").read_bytes(), b"owned vector data")
                self.assertTrue((root / "memory.sqlite3").exists())
                original.execute("INSERT INTO records VALUES ('legacy no longer used')")
                original.commit()
                self.assertEqual(prepare_memory_directory(root), target)
                with sqlite3.connect(target / "memory.sqlite3") as migrated:
                    self.assertEqual(migrated.execute("SELECT count(*) FROM records").fetchone()[0], 1)

    def test_live_qdrant_lock_refuses_migration_without_touching_source(self):
        import tempfile
        from pathlib import Path
        from backend.main_agent.profile_storage import prepare_memory_directory
        from backend.memory.stores.qdrant import QdrantLocalVectorStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = QdrantLocalVectorStore(path=root / "qdrant", vector_size=16)
            try:
                with self.assertRaisesRegex(RuntimeError, "vector_store_in_use"):
                    prepare_memory_directory(root)
                self.assertFalse((root / "memory" / "legacy-migration.json").exists())
                self.assertTrue((root / "qdrant").is_dir())
            finally:
                store.close()
            self.assertTrue((prepare_memory_directory(root) / "legacy-migration.json").exists())

    def test_ambiguous_existing_data_and_symlinks_are_preserved(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from backend.main_agent.profile_storage import prepare_memory_directory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "memory.sqlite3") as source:
                source.execute("CREATE TABLE original(value TEXT)")
            (root / "memory").mkdir()
            (root / "memory" / "private").write_text("preserve")
            with self.assertRaisesRegex(RuntimeError, "ambiguous"):
                prepare_memory_directory(root)
            self.assertEqual((root / "memory" / "private").read_text(), "preserve")


class ProfileSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_is_frozen_and_missing_resource_only_removes_visuals(self):
        import tempfile
        from pathlib import Path
        from backend.main_agent.lifecycle import ActiveProfileManager, ProfileResources
        from backend.main_agent.realtime import RealtimeHub
        from backend.tests.test_main_agent_realtime import FakeRuntime
        class Runtime(FakeRuntime):
            def __init__(self):
                super().__init__()
                self.tool_policy = ToolSurfacePolicy()
                self.busy = False
            def restart_busy(self):
                return self.busy
            async def update_soul(self, value):
                self.soul = value
            async def shutdown(self):
                pass
            async def wait_until_idle(self):
                pass
        built = []
        async def factory(identifier, path, soul):
            runtime = Runtime()
            runtime.soul = soul
            built.append(runtime)
            return ProfileResources(identifier, runtime, None, None)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "a" / "live2d" / "packages" / "v1" / "model"
            package.mkdir(parents=True)
            (package / "nod.motion3.json").write_text('{}')
            (package / "Test.model3.json").write_text('{}')
            hub = RealtimeHub()
            manager = ActiveProfileManager(root, hub, factory)
            first = await manager.activate("a", "Soul A", revision=1, adaptor={**adaptor(), "revision": 1}, model_version="v1")
            self.assertEqual(manager.runtime.tool_policy.avatar_capabilities.motion_names, ("nod",))
            self.assertEqual(manager.runtime.tool_policy.avatar_capabilities.expressions, ())
            self.assertEqual(await manager.activate("a", "Soul A", revision=1, adaptor={**adaptor(), "revision": 1}, model_version="v1"), first)
            self.assertEqual(len(built), 1)
            active_runtime = manager.runtime
            renewed = await manager.renew_session("a", 1)
            self.assertNotEqual(renewed, first)
            self.assertIs(manager.runtime, active_runtime)
            self.assertEqual(manager.runtime.soul, "Soul A")
            self.assertEqual(manager.runtime.tool_policy.avatar_capabilities.revision, 1)
            manager.runtime.busy = True
            with self.assertRaisesRegex(RuntimeError, "profile_busy"):
                await manager.activate("b", "Soul B", revision=1)
            self.assertEqual(manager.active_profile_id, "a")
            await manager.activate("b", "Soul B", revision=1, force=True)
            self.assertEqual(manager.active_profile_id, "b")
            self.assertEqual(manager.runtime.tool_policy.avatar_capabilities.motion_names, ())
            await manager.shutdown()

    async def test_profile_and_model_package_symlinks_are_rejected_before_factory(self):
        import tempfile
        from pathlib import Path
        from backend.main_agent.lifecycle import ActiveProfileManager
        from backend.main_agent.realtime import RealtimeHub
        async def factory(*args):
            self.fail("unsafe path reached factory")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a").mkdir()
            (root / "b").symlink_to(root / "a", target_is_directory=True)
            manager = ActiveProfileManager(root, RealtimeHub(), factory)
            with self.assertRaisesRegex(ValueError, "symlink"):
                await manager.activate("b", "Soul B", revision=1)
            (root / "a" / "live2d").symlink_to(root, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                await manager.activate("a", "Soul A", revision=1, model_version="v1")


class SayTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_runtime_harness_and_hub_preserve_scope_until_all_text_clauses_ack(self):
        import tempfile
        from pathlib import Path
        from backend.main_agent.context import ContextStore
        from backend.main_agent.lifecycle import ProfileResources
        from backend.main_agent.realtime import RealtimeHub
        from backend.main_agent.runtime import MainAgentRuntime, RendererClause
        from backend.main_agent.snapshot import SnapshotProjector
        from backend.tests.test_main_agent_realtime import FakeSocket
        tail_entered, tail_release = asyncio.Event(), asyncio.Event()
        async def provider(request):
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "say1", "function": {
                "name": "say_to_user", "arguments": '{"avatar":{"motion":"nod","expression":"smile"},"text":"第一句。第二句。"}'}}]}}]}
            tail_entered.set()
            await tail_release.wait()
        with tempfile.TemporaryDirectory() as directory:
            context = ContextStore(Path(directory) / "context.sqlite3")
            hub = RealtimeHub()
            runtime = MainAgentRuntime(context_store=context, snapshot_projector=SnapshotProjector(),
                tool_policy=ToolSurfacePolicy(avatar_capabilities=AvatarCapabilities.compile(adaptor(), profile_id="a", revision=7)),
                provider=provider, renderer=hub.render, executors={}, soul="Soul A", owner_profile_id="a")
            runtime.generation = 3
            resources = ProfileResources("a", runtime, None, None, generation=3, revision=7)
            await runtime.startup()
            await hub.set_active("a", 3, resources)
            socket = FakeSocket()
            session = await hub.attach(socket)
            await hub.command(session, {"type": "text.submit", "command_id": "input", "text": "hello"})
            async def wait_events(kind, number):
                for _ in range(300):
                    events = [event for event in socket.sent if isinstance(event, dict) and event.get("type") == kind]
                    if len(events) >= number:
                        return events
                    await asyncio.sleep(.001)
                self.fail(f"missing {kind}")
            first = (await wait_events("clause.offer", 1))[0]
            self.assertEqual((first["profile_id"], first["generation"], first["config_revision"]), ("a", 3, 7))
            self.assertEqual(first["avatar"], {"motion": "nod", "expression": "smile"})
            self.assertFalse(any(event.get("type") == "say.closed" for event in socket.sent if isinstance(event, dict)))
            await hub.renderer_ack(session, first["clause_id"], complete=True)
            second = (await wait_events("clause.offer", 2))[1]
            self.assertEqual(second["say_id"], first["say_id"])
            self.assertNotEqual(second["clause_id"], first["clause_id"])
            await hub.renderer_ack(session, second["clause_id"], complete=True)
            closed = (await wait_events("say.closed", 1))[0]
            self.assertEqual(closed["say_id"], first["say_id"])
            self.assertEqual(closed["clause_count"], 2)
            await asyncio.wait_for(tail_entered.wait(), 1)
            tail_release.set()
            await runtime.wait_until_idle()
            # A late producer from a different Profile cannot be rebound to this socket.
            port = await hub.acquire_renderer_port()
            stale = RendererClause("old", "private B", "inv", "in", 1, "b", 2, 7, "s", "c", {})
            self.assertEqual(await port.offer_clause(stale), "stale_avatar_scope")
            self.assertFalse(any("private B" in str(event) for event in socket.sent))
            self.assertEqual([turn["content"] for turn in context.fold().conversation_turns][-2:], ["第一句。", "第二句。"])
            await hub.stop_accepting()
            await runtime.shutdown()
            context.close()


class DeleteGuardTests(unittest.TestCase):
    def test_delete_checks_real_context_and_task_ownership_without_starting_dormant_runtime(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.main_agent.context import ContextStore
        from backend.main_agent.lifecycle import ActiveProfileManager
        from backend.main_agent.realtime import RealtimeHub, router
        from backend.task.models import TaskPage
        async def factory(*args):
            self.fail("delete status must not activate runtime")
        class Tasks:
            owner = None
            async def list_tasks(self, **kwargs):
                return TaskPage((object(),) if kwargs["owner_profile_id"] == self.owner else (), None)
        with tempfile.TemporaryDirectory() as directory:
            hub, tasks = RealtimeHub(), Tasks()
            profiles = ActiveProfileManager(Path(directory), hub, factory)
            app = FastAPI()
            app.state.main_agent_realtime = hub
            app.state.main_agent_profiles = profiles
            app.state.task_manager = tasks
            app.include_router(router)
            client = TestClient(app)
            headers = {"origin": "http://127.0.0.1:6400"}
            def check(identifier):
                return client.get(f"/api/v1/main-agent/profile/{identifier}/delete-check", headers=headers).json()
            self.assertTrue(check("a")["allowed"])
            tasks.owner = "a"
            self.assertEqual(check("a")["reason"], "unfinished_tasks")
            self.assertTrue(check("b")["allowed"])
            context = ContextStore(Path(directory) / "b" / "context.sqlite3")
            context.append_event("user_segment", {"segment_id": "b1", "input_id": "b", "text": "private B"})
            context.close()
            self.assertEqual(check("b")["reason"], "unarchived_context")
            profiles._retiring["a"] = object()
            self.assertEqual(check("a")["reason"], "profile_cleanup_incomplete")


class ParserRejectionTests(unittest.TestCase):
    def test_complete_invalid_envelopes_never_emit_early_text(self):
        from backend.main_agent.tool_runtime.stream import ToolCallAssembler
        for raw in ('{"text":"No。","avatar":{"motion":"nod","motion":"bad"}}',
                    '{"text":"No。","avatar":NaN}', '[{"text":"No。"}]',
                    '{"text":"No。","extra":true}', '{"avatar":{"text":"fake。"},"text":42}'):
            parser = ToolCallAssembler()
            clauses = parser.feed({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "s", "function": {
                "name": "say_to_user", "arguments": raw}}]}}]})
            self.assertEqual(clauses, (), raw)
            self.assertIsNotNone(parser.finish()[0].parse_error, raw)

    def test_capability_limits_types_and_path_boundaries_reject_consistently(self):
        from copy import deepcopy
        for mutate in (
            lambda value: value["entries"].update({"none": value["entries"]["nod"]}),
            lambda value: value["entries"]["nod"]["source"].update(file="../b/nod.motion3.json"),
            lambda value: value["entries"]["nod"]["source"].update(file="model/%2e%2e/nod.motion3.json"),
            lambda value: value["entries"]["nod"]["playback"].update(mode="hold"),
            lambda value: value["entries"]["nod"]["playback"].update(fade_in_ms=float("inf")),
            lambda value: value["entries"]["nod"].update(description="x" * 257),
            lambda value: value["states"].update(thinking="nod"),
        ):
            value = deepcopy(adaptor())
            mutate(value)
            with self.assertRaises(ValueError):
                AvatarCapabilities.compile(value)
        value = adaptor()
        value["entries"] = {f"gesture_{index}": dict(value["entries"]["nod"]) for index in range(33)}
        with self.assertRaisesRegex(ValueError, "agent_entry_limit"):
            AvatarCapabilities.compile(value)


class LateEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_late_invalid_json_cancels_scope_without_retracting_delivered_receipt(self):
        policy = ToolSurfacePolicy()
        grant = ResponseGrant("g", "i", 1, 1)
        calls, endings = [], []
        async def execute(call):
            calls.append(call.arguments["text"])
        async def close(event):
            endings.append(event)
        harness = ToolHarness(policy, {"say_to_user": execute},
            state_provider=lambda: EffectFence("inv", "i", 1, 1, grant), say_closed_hook=close)
        surface = harness.freeze_surface(InvokeMode.RESPOND, input_id="i", revision=1, context_version=1,
            response_grant=grant, current_response_grant=grant)
        async def provider():
            for fragment in ('{"text":"already heard。"}', ' garbage'):
                yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "s", "function": {
                    "name": "say_to_user", "arguments": fragment}}]}}]}
        result = await harness.run(provider(), invoke_id="inv", tool_surface=surface)
        self.assertEqual(calls, ["already heard。"])
        self.assertEqual([event["cancelled"] for event in endings], [False, True])
        self.assertEqual(len(result.streamed_speech), 1)
        self.assertFalse(result.receipts[0].accepted)

    async def test_cancel_only_selected_say_before_port_acquisition_preserves_other_queue_jobs(self):
        from types import SimpleNamespace
        from backend.main_agent.speech import ClauseSpeechDelivery
        queued, cancelled = [], []
        acquired = asyncio.Event()
        async def no_port_yet():
            acquired.set()
            await asyncio.Event().wait()
        class Runtime:
            async def renderer_tts_queued(self, clause_id):
                queued.append(clause_id)
            async def renderer_ack(self, clause_id, **kwargs):
                cancelled.append(clause_id)
        delivery = ClauseSpeechDelivery(None, no_port_yet, Runtime())
        await delivery(SimpleNamespace(clause_id="a1", say_id="a", text="a"))
        await delivery(SimpleNamespace(clause_id="b1", say_id="b", text="b"))
        await acquired.wait()
        await delivery.cancel_current("invalid", say_id="a")
        self.assertIn("a1", cancelled)
        self.assertNotIn("b1", cancelled)
        await delivery.shutdown()


class ResidentPoolTests(unittest.TestCase):
    """Activation accepts bounded mixed pools without exposing runtime motion loops to tools."""

    def test_mixed_pool_and_empty_description(self):
        value = adaptor()
        value['schema_version'] = 2
        value['entries']['idle'] = {**value['entries']['nod'], 'agent_enabled': False, 'playback': {'mode': 'loop'}}
        value['entries']['nod']['description'] = ''
        value['entries']['nod']['display_name'] = 'Nod'
        value['states'] = {'idle': ['idle', 'smile'], 'thinking': ['smile'], 'listening': [], 'speaking': []}
        compiled = AvatarCapabilities.compile(value)
        self.assertNotIn('idle', compiled.motion_names)
        self.assertEqual(dict(compiled.motions)['nod'], 'Nod')

    def test_invalid_pool_shapes(self):
        for pool in ('smile', None, ['smile', 'smile'], ['missing'], ['nod'], ['smile'] * 9, [False]):
            value = adaptor()
            value['schema_version'] = 2
            value['states'] = {'idle': pool, 'thinking': [], 'listening': [], 'speaking': []}
            with self.assertRaises(ValueError, msg=repr(pool)):
                AvatarCapabilities.compile(value)
