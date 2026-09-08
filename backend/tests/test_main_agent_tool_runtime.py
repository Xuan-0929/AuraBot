"""Checks Main Agent tools through the exposed capability, stream, and effect boundaries.

The suite protects mode/grant exposure, the text-only incremental say parser, effect grouping, fencing,
say-first execution before the provider finishes later tools, and detached Observation callbacks. Removed schema self-validation, motion tokens,
alternate provider event dialects, private caches, and thread-based limiter internals are not tested.
"""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import AsyncIterator
from typing import Any

from backend.main_agent.tool_runtime import (
    EffectFence,
    InvokeMode,
    ResponseGrant,
    ToolHarness,
    ToolSurfacePolicy,
)
from backend.main_agent.tool_runtime.stream import ToolCallAssembler, segment_say_text


def chunks(*calls: tuple[int, str, str, str]) -> AsyncIterator[dict[str, Any]]:
    async def stream() -> AsyncIterator[dict[str, Any]]:
        yield {
            "choices": [{"delta": {"tool_calls": [
                {"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}
                for index, call_id, name, arguments in calls
            ]}}]
        }
    return stream()


class ToolSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ToolSurfacePolicy(
            workspace_ids=("repo",),
            observation_tools=("web_search", "web_read"),
            task_controls=("inspect_tasks", "cancel_task"),
        )

    def test_prepare_exposes_reads_and_note_but_no_say_or_durable_action(self) -> None:
        surface = self.policy.freeze_surface(
            InvokeMode.PREPARE, input_id="i1", revision=1, context_version=2,
        )
        self.assertIn("web_search", surface.names)
        self.assertIn("workspace_read", surface.names)
        self.assertIn("note_think_checkpoint", surface.names)
        self.assertNotIn("say_to_user", surface.names)
        self.assertNotIn("task_assign", surface.names)

    def test_current_grant_exposes_text_only_say_and_durable_tools(self) -> None:
        grant = ResponseGrant("g1", "i1", 1, 2)
        surface = self.policy.freeze_surface(
            InvokeMode.RESPOND,
            input_id="i1",
            revision=1,
            context_version=2,
            response_grant=grant,
            current_response_grant=grant,
        )
        self.assertIn("say_to_user", surface.names)
        self.assertIn("task_assign", surface.names)
        say = next(item for item in surface.provider_schemas() if item["function"]["name"] == "say_to_user")
        self.assertEqual(tuple(say["function"]["parameters"]["properties"]), ("text",))
        self.assertNotIn("motion_token", str(surface.provider_schemas()))


class StreamingParserTests(unittest.TestCase):
    def test_text_only_say_streams_across_every_json_and_escape_boundary(self) -> None:
        assembler = ToolCallAssembler()
        raw = '{"text":"你好，world\\n下一句。"}'
        emitted: list[str] = []
        for index, fragment in enumerate(raw):
            emitted.extend(clause.text for clause in assembler.feed({
                "choices": [{"delta": {"tool_calls": [{
                    "index": 0,
                    "id": "call-1" if index == 0 else None,
                    "function": {"name": "say_to_user" if index == 0 else None, "arguments": fragment},
                }]}}],
            }))
            for text in emitted[-1:]:
                stream = assembler.say_stream(0)
                if stream and stream.remaining_text.startswith(text):
                    assembler.accept_say_clause(0, text)
        call = assembler.finish()[0]
        self.assertEqual(call.arguments, {"text": "你好，world\n下一句。"})
        self.assertEqual(segment_say_text(call.arguments["text"], final=True), ("你好，world\n", "下一句。"))

    def test_duplicate_key_malformed_json_and_unpaired_surrogate_are_rejected(self) -> None:
        for raw, code in (
            ('{"text":"a","text":"b"}', "duplicate_json_key"),
            ('{"text":', "malformed_arguments"),
            ('{"text":"\\ud800"}', "invalid_unicode_surrogate"),
        ):
            assembler = ToolCallAssembler()
            assembler.feed({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": "c", "function": {"name": "say_to_user", "arguments": raw},
            }]}}]})
            self.assertEqual(assembler.finish()[0].parse_error, code)


class ToolHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.policy = ToolSurfacePolicy(
            workspace_ids=("repo",), observation_tools=("web_search",),
        )
        self.grant = ResponseGrant("g", "input", 1, 1)
        self.surface = self.policy.freeze_surface(
            InvokeMode.RESPOND,
            input_id="input",
            revision=1,
            context_version=1,
            response_grant=self.grant,
            current_response_grant=self.grant,
        )
        self.fence = lambda: EffectFence(
            "invoke", "input", 1, 1, response_grant=self.grant,
        )

    async def test_first_say_clause_is_delivered_before_remaining_tool_stream(self) -> None:
        spoken = asyncio.Event()
        release = asyncio.Event()
        delivered = []

        async def execute(call):
            delivered.append((call.name, dict(call.arguments)))
            if call.name == "say_to_user":
                spoken.set()
            return {"ok": True}

        async def stream():
            async for chunk in chunks((0, "say", "say_to_user", '{"text":"我查一下。')):
                yield chunk
            await release.wait()
            async for chunk in chunks(
                (0, "say", "say_to_user", '"}'),
                (1, "read", "web_search", '{"query":"Aura"}'),
            ):
                yield chunk

        harness = ToolHarness(
            self.policy, {"say_to_user": execute, "web_search": execute},
            state_provider=self.fence,
            observation_acceptance_hook=lambda proposed, _call: proposed,
        )
        running = asyncio.create_task(harness.run(stream(), invoke_id="invoke", tool_surface=self.surface))
        try:
            await asyncio.wait_for(spoken.wait(), 1)
            self.assertFalse(running.done())
            self.assertEqual(delivered, [("say_to_user", {"text": "我查一下。"})])
        finally:
            release.set()
            settlement = await running
            await harness.wait_for_observations()
        self.assertTrue(all(receipt.accepted for receipt in settlement.receipts))
        self.assertEqual([name for name, _arguments in delivered], ["say_to_user", "web_search"])

    async def test_say_executes_before_safe_read_and_observation_detaches(self) -> None:
        order: list[str] = []
        completed: list[Any] = []

        async def execute(call: Any) -> Any:
            order.append(call.name)
            return {"ok": True}

        harness = ToolHarness(
            self.policy,
            {"say_to_user": execute, "web_search": execute},
            state_provider=self.fence,
            observation_acceptance_hook=lambda proposed, _call: proposed,
            observation_completion_hook=lambda outcome: completed.append(outcome),
        )
        settlement = await harness.run(
            chunks(
                (0, "read", "web_search", '{"query":"Aura"}'),
                (1, "say", "say_to_user", '{"text":"我查一下。"}'),
            ),
            invoke_id="invoke",
            tool_surface=self.surface,
        )
        await harness.wait_for_observations()

        self.assertEqual(order, ["say_to_user", "web_search"])
        self.assertEqual([receipt.code for receipt in settlement.receipts], ["detached", "accepted"])
        self.assertEqual(len(completed), 1)

    async def test_safe_read_and_durable_action_are_rejected_as_a_group(self) -> None:
        executed: list[str] = []
        harness = ToolHarness(
            self.policy,
            {"web_search": lambda call: executed.append(call.name), "task_assign": lambda call: executed.append(call.name)},
            state_provider=self.fence,
        )
        settlement = await harness.run(
            chunks(
                (0, "read", "web_search", '{"query":"Aura"}'),
                (1, "task", "task_assign", '{"title":"t","prompt":"p","workspace_id":"repo"}'),
            ),
            invoke_id="invoke",
            tool_surface=self.surface,
        )
        self.assertEqual(executed, [])
        self.assertTrue(all(receipt.code == "illegal_effect_combination" for receipt in settlement.receipts))

    async def test_stale_effect_fence_rejects_speech(self) -> None:
        harness = ToolHarness(
            self.policy,
            {"say_to_user": lambda _call: None},
            state_provider=lambda: EffectFence("other", "input", 1, 1, response_grant=self.grant),
        )
        settlement = await harness.run(
            chunks((0, "say", "say_to_user", '{"text":"hello"}')),
            invoke_id="invoke",
            tool_surface=self.surface,
        )
        self.assertEqual(settlement.receipts[0].code, "stale_invoke")
