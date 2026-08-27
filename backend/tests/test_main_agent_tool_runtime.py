"""Exercise the Main Agent tool surface, stream parser, and fenced in-process execution lifecycle.

The tests feed real provider-shaped chunks through ToolHarness and assert only observable receipts,
executor effects, and settlements.  They deliberately keep Observation, Task, and speech adapters as
injected callables because later tasks own those concrete integrations.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace

from backend.main_agent.tool_runtime import (
    EffectFence,
    InvokeMode,
    ObservationOutcome,
    ObservationLimiter,
    ResponseGrant,
    SayTextStream,
    ToolCallAssembler,
    ToolCall,
    ToolHarness,
    ToolSurfacePolicy,
)


def delta(index: int, call_id: str, name: str, arguments: str) -> dict:
    return {
        "choices": [{"delta": {"tool_calls": [{
            "index": index, "id": call_id,
            "function": {"name": name, "arguments": arguments},
        }]}}]
    }


class ToolSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ToolSurfacePolicy(workspace_ids=("repo",), motion_tokens=("happy",))
        self.grant = ResponseGrant("grant", "input", 2, 8)

    def freeze(self, mode, *, grant=None, current_grant=None, speech_candidate=False,
               confirmed_speech=False):
        return self.policy.freeze_surface(
            mode, input_id="input", revision=2, context_version=8,
            response_grant=grant, current_response_grant=current_grant,
            speech_candidate=speech_candidate, confirmed_speech=confirmed_speech,
        )

    def test_prepare_physically_omits_say_and_durable_schemas(self) -> None:
        names = {item["function"]["name"] for item in self.freeze(InvokeMode.PREPARE).provider_schemas()}
        self.assertIn("note_think_checkpoint", names)
        self.assertIn("web_search", names)
        self.assertNotIn("say_to_user", names)
        self.assertNotIn("task_assign", names)

    def test_response_effect_schemas_require_a_current_grant_and_no_speech(self) -> None:
        without_grant = {item["function"]["name"] for item in self.freeze(
            InvokeMode.RESPOND
        ).provider_schemas()}
        allowed = {
            item["function"]["name"]
            for item in self.freeze(
                InvokeMode.RESPOND, grant=self.grant, current_grant=self.grant
            ).provider_schemas()
        }
        speech = {
            item["function"]["name"]
            for item in self.freeze(
                InvokeMode.CONTINUE, grant=self.grant, current_grant=self.grant,
                speech_candidate=True,
            ).provider_schemas()
        }
        self.assertNotIn("say_to_user", without_grant)
        self.assertIn("say_to_user", allowed)
        self.assertIn("task_assign", allowed)
        self.assertNotIn("say_to_user", speech)
        self.assertNotIn("task_assign", speech)
        say = next(item for item in self.freeze(
            InvokeMode.RESPOND, grant=self.grant, current_grant=self.grant
        ).provider_schemas() if item["function"]["name"] == "say_to_user")
        self.assertEqual(say["function"]["parameters"]["properties"]["motion_token"]["enum"], ["happy"])

    def test_empty_dynamic_registries_omit_workspace_tools_and_avatar_fields(self) -> None:
        surface = ToolSurfacePolicy().freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        ).provider_schemas()
        functions = {item["function"]["name"]: item["function"] for item in surface}
        self.assertNotIn("task_assign", functions)
        self.assertNotIn("workspace_list", functions)
        self.assertNotIn("workspace_read", functions)
        say_properties = functions["say_to_user"]["parameters"]["properties"]
        self.assertEqual(set(say_properties), {"text"})

    def test_grant_coordinates_and_current_identity_are_validated_before_exposure(self) -> None:
        wrong_coordinates = ResponseGrant("grant", "input", 3, 8)
        stale_identity = ResponseGrant("new", "input", 2, 8)
        for grant, current in (
            (wrong_coordinates, wrong_coordinates),
            (self.grant, stale_identity),
        ):
            names = {
                item["function"]["name"]
                for item in self.freeze(
                    InvokeMode.RESPOND, grant=grant, current_grant=current
                ).provider_schemas()
            }
            self.assertNotIn("say_to_user", names)
            self.assertNotIn("task_assign", names)


class StreamingParserTests(unittest.TestCase):
    def test_assembles_interleaved_calls_from_arbitrary_provider_chunks(self) -> None:
        parser = ToolCallAssembler()
        parser.feed(delta(0, "say", "say_to_user", '{"te'))
        parser.feed(delta(1, "read", "web_search", '{"query":"Au'))
        parser.feed(delta(0, "", "", 'xt":"你好。再'))
        parser.feed(delta(1, "", "", 'raBot"}'))
        parser.feed(delta(0, "", "", '见"}'))
        calls = parser.finish()
        self.assertEqual([(call.call_id, call.name) for call in calls], [
            ("say", "say_to_user"), ("read", "web_search")
        ])
        self.assertEqual(calls[0].arguments, {"text": "你好。再见"})
        self.assertEqual(calls[1].arguments, {"query": "AuraBot"})

    def test_extracts_only_stable_say_clauses_across_escapes_and_surrogate_pairs(self) -> None:
        parser = SayTextStream()
        self.assertEqual(parser.feed('{"text":"你\\u597d'), ())
        self.assertEqual(parser.feed('\\n世界。表情\\uD83D'), ("你好\n", "世界。"))
        self.assertEqual(parser.feed('\\uDE00，继续\\\"说"}'), ())
        self.assertEqual(parser.complete_text, '你好\n世界。表情😀，继续"说')
        self.assertEqual(parser.remaining_text, '表情😀，继续"说')

    def test_malformed_or_incomplete_json_is_rejected_at_finish(self) -> None:
        parser = ToolCallAssembler()
        parser.feed(delta(0, "bad", "web_search", '{"query":'))
        calls = parser.finish()
        self.assertEqual(calls[0].parse_error, "malformed_arguments")

    def test_rejects_duplicate_keys_and_unpaired_surrogates(self) -> None:
        duplicate = ToolCallAssembler()
        duplicate.feed(delta(0, "dup", "say_to_user", '{"text":"一。","text":"二。"}'))
        high = ToolCallAssembler()
        high.feed(delta(0, "high", "say_to_user", '{"text":"\\uD83D。"}'))
        low = ToolCallAssembler()
        low.feed(delta(0, "low", "say_to_user", '{"text":"\\uDE00。"}'))
        self.assertEqual(duplicate.finish()[0].parse_error, "duplicate_json_key")
        self.assertEqual(high.finish()[0].parse_error, "invalid_unicode_surrogate")
        self.assertEqual(low.finish()[0].parse_error, "invalid_unicode_surrogate")
        literal = SayTextStream()
        self.assertEqual(literal.feed('{"text":"\ud800。'), ())
        self.assertTrue(literal.invalid)

    def test_split_identity_uses_full_id_and_conflicts_are_rejected(self) -> None:
        parser = ToolCallAssembler()
        parser.feed({"tool_call": {
            "index": 0, "call_id_delta": "call_", "name": "web_search",
            "arguments_delta": '{"query":"x"',
        }})
        parser.feed({"tool_call": {
            "index": 0, "call_id_delta": "123", "identity_complete": True,
            "arguments_delta": "}",
        }})
        self.assertEqual(parser.finish()[0].call_id, "call_123")

        conflict = ToolCallAssembler()
        conflict.feed({"tool_call": {
            "index": 0, "call_id": "same", "name": "web_search",
            "arguments": '{"query":"a"}',
        }})
        conflict.feed({"tool_call": {
            "index": 1, "call_id": "same", "name": "web_search",
            "arguments": '{"query":"b"}',
        }})
        self.assertEqual(
            {call.parse_error for call in conflict.finish()}, {"ambiguous_call_identity"}
        )

    def test_unknown_responses_delta_stays_poisoned_after_late_item_added(self) -> None:
        parser = ToolCallAssembler()
        parser.feed({
            "type": "response.function_call_arguments.delta",
            "item_id": "late-item", "output_index": 0, "delta": '{"query":"x"}',
        })
        parser.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {
                "type": "function_call", "id": "late-item", "call_id": "call-late",
                "name": "web_search", "arguments": "",
            },
        })
        self.assertEqual(parser.finish()[0].parse_error, "provider_stream_error")

    def test_responses_delta_requires_explicit_item_id_and_string_delta(self) -> None:
        call_id_substitute = ToolCallAssembler()
        call_id_substitute.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": ""},
        })
        call_id_substitute.feed({
            "type": "response.function_call_arguments.delta", "call_id": "item",
            "output_index": 0, "delta": '{"query":"x"}',
        })
        self.assertEqual(
            call_id_substitute.finish()[0].parse_error, "provider_stream_error"
        )

        non_string_delta = ToolCallAssembler()
        non_string_delta.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": ""},
        })
        non_string_delta.feed({
            "type": "response.function_call_arguments.delta", "item_id": "item",
            "output_index": 0, "delta": {"query": "x"},
        })
        self.assertEqual(non_string_delta.finish()[0].parse_error, "provider_stream_error")

    def test_responses_done_reconciles_identity_name_and_does_not_duplicate_arguments(self) -> None:
        valid = ToolCallAssembler()
        valid.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": ""},
        })
        valid.feed({
            "type": "response.function_call_arguments.delta", "item_id": "item",
            "output_index": 0, "delta": '{"query":"x"}',
        })
        valid.feed({
            "type": "response.output_item.done", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": '{"query":"x"}'},
        })
        call = valid.finish()[0]
        self.assertIsNone(call.parse_error)
        self.assertEqual(call.arguments, {"query": "x"})

        changed = ToolCallAssembler()
        changed.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "before",
                     "name": "web_search", "arguments": ""},
        })
        changed.feed({
            "type": "response.output_item.done", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "after",
                     "name": "web_read", "arguments": '{"source_id":"x"}'},
        })
        self.assertEqual(changed.finish()[0].parse_error, "ambiguous_call_identity")

    def test_responses_done_requires_exact_added_item_mapping(self) -> None:
        done_only = ToolCallAssembler()
        done_only.feed({
            "type": "response.output_item.done", "output_index": 0,
            "item": {"type": "function_call", "id": "unknown", "call_id": "call",
                     "name": "web_search", "arguments": '{"query":"x"}'},
        })
        self.assertEqual(done_only.finish(), ())
        self.assertEqual(done_only.protocol_error, "provider_stream_error")

        changed_item = ToolCallAssembler()
        changed_item.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item-before", "call_id": "call",
                     "name": "web_search", "arguments": ""},
        })
        changed_item.feed({
            "type": "response.output_item.done", "output_index": 0,
            "item": {"type": "function_call", "id": "item-after", "call_id": "call",
                     "name": "web_search", "arguments": '{"query":"x"}'},
        })
        self.assertEqual(changed_item.finish()[0].parse_error, "provider_stream_error")

        changed_index = ToolCallAssembler()
        changed_index.feed({
            "type": "response.output_item.added", "output_index": 0,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": ""},
        })
        changed_index.feed({
            "type": "response.output_item.done", "output_index": 1,
            "item": {"type": "function_call", "id": "item", "call_id": "call",
                     "name": "web_search", "arguments": '{"query":"x"}'},
        })
        self.assertEqual(changed_index.finish()[0].parse_error, "provider_stream_error")

    def test_incremental_text_rejects_unescaped_controls_and_known_length_overflow(self) -> None:
        control = SayTextStream()
        self.assertEqual(control.feed('{"text":"no\x01。'), ())
        self.assertTrue(control.invalid)
        too_long = SayTextStream(max_length=8_000)
        self.assertEqual(too_long.feed('{"text":"先。' + "x" * 8_000), ())
        self.assertTrue(too_long.invalid)


class ToolHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.grant = ResponseGrant("grant", "input", 2, 8)
        self.fence = EffectFence(
            active_invoke_id="invoke", input_id="input", revision=2, context_version=8,
            response_grant=self.grant,
        )
        self.effects: list[tuple[str, dict]] = []

        async def execute(call):
            self.effects.append((call.name, dict(call.arguments)))
            return {"accepted": True}

        self.executors = {
            name: execute for name in (
                "say_to_user", "note_think_checkpoint", "web_search", "web_read",
                "workspace_list", "workspace_read", "task_assign",
            )
        }

    def harness(self, *, state_provider=True, observation_completion_hook=None,
                cancel_tombstone_limit=256, **policy_options) -> ToolHarness:
        policy = ToolSurfacePolicy(workspace_ids=("repo",), **policy_options)
        provider = (lambda: self.fence) if state_provider else None
        return ToolHarness(
            policy, self.executors, state_provider=provider,
            observation_completion_hook=observation_completion_hook,
            cancel_tombstone_limit=cancel_tombstone_limit,
        )

    async def invoke(self, chunks, *, mode=InvokeMode.RESPOND, grant=True, harness=None):
        active_harness = harness or self.harness()
        supplied_grant = self.grant if grant else None
        surface = active_harness.policy.freeze_surface(
            mode, input_id="input", revision=2, context_version=8,
            response_grant=supplied_grant, current_response_grant=supplied_grant,
        )
        return await active_harness.run(
            chunks, invoke_id="invoke", tool_surface=surface,
        )

    async def test_runtime_revalidates_a_fabricated_forbidden_prepare_call(self) -> None:
        settlement = await self.invoke(
            [delta(0, "say", "say_to_user", '{"text":"no"}')],
            mode=InvokeMode.PREPARE, grant=False,
        )
        self.assertEqual(settlement.receipts[0].code, "tool_not_exposed")
        self.assertEqual(self.effects, [])

    async def test_frozen_surface_cannot_be_broadened_by_later_runtime_state(self) -> None:
        harness = self.harness()
        surface = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
            speech_candidate=True,
        )
        settlement = await harness.run(
            [delta(0, "say", "say_to_user", '{"text":"no"}')],
            invoke_id="invoke", tool_surface=surface,
        )
        self.assertEqual(settlement.receipts[0].code, "tool_not_exposed")
        self.assertEqual(self.effects, [])

    async def test_tampered_frozen_surface_is_rejected_before_streaming(self) -> None:
        harness = self.harness()
        surface = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        )
        prepare = harness.policy.freeze_surface(
            InvokeMode.PREPARE, input_id="input", revision=2, context_version=8,
        )
        forged = replace(prepare, schema_records=surface.schema_records)
        settlement = await harness.run(
            [delta(0, "say", "say_to_user", '{"text":"no。"}')],
            invoke_id="invoke", tool_surface=forged,
        )
        self.assertEqual(settlement.receipts[0].code, "invalid_tool_surface")
        self.assertEqual(self.effects, [])

    async def test_malformed_surface_fields_settle_invalid_and_cleanup_allows_retry(self) -> None:
        harness = self.harness()
        valid = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        )
        malformed = replace(valid, revision=True)
        rejected = await harness.run(
            [delta(0, "say", "say_to_user", '{"text":"no。"}')],
            invoke_id="invoke", tool_surface=malformed,
        )
        accepted = await harness.run(
            [delta(0, "say", "say_to_user", '{"text":"yes。"}')],
            invoke_id="invoke", tool_surface=valid,
        )
        self.assertEqual(rejected.receipts[0].code, "invalid_tool_surface")
        self.assertEqual(accepted.receipts[0].code, "accepted")
        self.assertEqual(self.effects, [("say_to_user", {"text": "yes。"})])

    async def test_missing_state_provider_fails_closed_for_note_read_and_say(self) -> None:
        harness = self.harness(state_provider=False)
        prepare = await self.invoke([
            delta(0, "note", "note_think_checkpoint", '{"current_understanding":"x"}'),
            delta(1, "read", "web_search", '{"query":"x"}'),
        ], mode=InvokeMode.PREPARE, grant=False, harness=harness)
        respond = await self.invoke([
            delta(0, "say", "say_to_user", '{"text":"x"}'),
        ], harness=harness)
        self.assertEqual({receipt.code for receipt in prepare.receipts}, {"fence_unavailable"})
        self.assertEqual(respond.receipts[0].code, "fence_unavailable")
        self.assertEqual(self.effects, [])

    async def test_broken_state_provider_fails_closed_and_still_settles(self) -> None:
        def broken_state():
            raise RuntimeError("scheduler unavailable")

        harness = ToolHarness(
            ToolSurfacePolicy(), self.executors, state_provider=broken_state
        )
        settlement = await self.invoke([
            delta(0, "read", "web_search", '{"query":"x"}'),
        ], harness=harness)
        self.assertTrue(settlement.settled)
        self.assertEqual(settlement.receipts[0].code, "fence_unavailable")
        self.assertEqual(self.effects, [])

    async def test_stale_invoke_revision_grant_and_speech_are_fenced(self) -> None:
        scenarios = (
            (EffectFence("other", "input", 2, 8, self.grant), "stale_invoke"),
            (EffectFence("invoke", "input", 3, 9, self.grant), "stale_revision"),
            (EffectFence("invoke", "input", 2, 8, ResponseGrant("new", "input", 2, 8)), "invalid_response_grant"),
            (EffectFence("invoke", "input", 2, 8, self.grant, speech_candidate=True), "speech_fenced"),
            (EffectFence("invoke", "input", 2, 8, self.grant, confirmed_speech=True), "speech_fenced"),
        )
        for current, expected in scenarios:
            self.fence = current
            settlement = await self.invoke([delta(0, "say", "say_to_user", '{"text":"no"}')])
            self.assertEqual(settlement.receipts[0].code, expected)
        self.assertEqual(self.effects, [])

    async def test_schema_and_total_call_limits_reject_without_execution(self) -> None:
        chunks = [
            delta(0, "bad", "web_search", '{"query":7}'),
            delta(1, "one", "web_search", '{"query":"one"}'),
            delta(2, "two", "web_search", '{"query":"two"}'),
        ]
        settlement = await self.invoke(chunks, harness=self.harness(max_calls=2))
        codes = {receipt.call_id: receipt.code for receipt in settlement.receipts}
        self.assertEqual(codes, {
            "bad": "schema_invalid", "one": "detached", "two": "call_limit_exceeded"
        })
        await asyncio.sleep(0)
        self.assertEqual(self.effects, [("web_search", {"query": "one"})])

    async def test_abnormal_provider_end_settles_and_executes_no_staged_non_say(self) -> None:
        async def chunks():
            yield delta(0, "read", "web_search", '{"query":"x"}')
            raise RuntimeError("provider exploded")

        settlement = await self.invoke(chunks())
        self.assertTrue(settlement.settled)
        self.assertEqual(settlement.receipts[0].code, "provider_stream_error")
        self.assertIn("provider_stream_error", {item.code for item in settlement.receipts})
        self.assertEqual(self.effects, [])

    async def test_responses_done_missing_arguments_rejects_staged_call(self) -> None:
        harness = self.harness()
        settlement = await self.invoke([
            {
                "type": "response.output_item.added", "output_index": 0,
                "item": {"type": "function_call", "id": "item", "call_id": "call",
                         "name": "web_search", "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta", "item_id": "item",
                "output_index": 0, "delta": '{"query":"x"}',
            },
            {
                "type": "response.output_item.done", "output_index": 0,
                "item": {"type": "function_call", "id": "item", "call_id": "call",
                         "name": "web_search"},
            },
        ], harness=harness)
        await harness.wait_for_observations()
        self.assertEqual(settlement.receipts[0].code, "provider_stream_error")
        self.assertEqual(self.effects, [])

    async def test_responses_non_function_done_rejects_staged_call(self) -> None:
        harness = self.harness()
        settlement = await self.invoke([
            {
                "type": "response.output_item.added", "output_index": 0,
                "item": {"type": "function_call", "id": "item", "call_id": "call",
                         "name": "web_search", "arguments": ""},
            },
            {
                "type": "response.function_call_arguments.delta", "item_id": "item",
                "output_index": 0, "delta": '{"query":"x"}',
            },
            {
                "type": "response.output_item.done", "output_index": 0,
                "item": {"type": "message", "id": "item"},
            },
        ], harness=harness)
        await harness.wait_for_observations()
        self.assertEqual(settlement.receipts[0].code, "provider_stream_error")
        self.assertEqual(self.effects, [])

    async def test_effect_combinations_and_durable_limit(self) -> None:
        illegal = await self.invoke([
            delta(0, "read", "web_search", '{"query":"x"}'),
            delta(1, "task", "task_assign", '{"title":"t","prompt":"p","workspace_id":"repo"}'),
        ])
        self.assertEqual({receipt.code for receipt in illegal.receipts}, {"illegal_effect_combination"})
        durable = await self.invoke([
            delta(0, "a", "task_assign", '{"title":"a","prompt":"p","workspace_id":"repo"}'),
            delta(1, "b", "task_assign", '{"title":"b","prompt":"p","workspace_id":"repo"}'),
        ])
        self.assertEqual({receipt.code for receipt in durable.receipts}, {"durable_limit_exceeded"})
        self.assertEqual(self.effects, [])

    async def test_say_executes_before_safe_reads_regardless_of_provider_order(self) -> None:
        settlement = await self.invoke([
            delta(0, "read", "web_search", '{"query":"x"}'),
            delta(1, "say", "say_to_user", '{"text":"先说。"}'),
        ])
        await asyncio.sleep(0)
        self.assertEqual([name for name, _ in self.effects], ["say_to_user", "web_search"])
        self.assertEqual({receipt.code for receipt in settlement.receipts}, {"accepted", "detached"})

    async def test_clauses_decoded_before_split_identity_and_name_deliver_once_in_order(self) -> None:
        settlement = await self.invoke([
            {"tool_call": {"index": 0, "arguments_delta": '{"text":"一。二。'}},
            {"tool_call": {"index": 0, "call_id_delta": "call_",
                           "name_delta": "say_to_"}},
            {"tool_call": {"index": 0, "call_id_delta": "one", "identity_complete": True,
                           "name_delta": "user", "name_complete": True}},
            {"tool_call": {"index": 0, "arguments_delta": '三。"}'}},
        ])
        self.assertEqual(settlement.receipts[0].code, "accepted")
        self.assertEqual(
            self.effects,
            [("say_to_user", {"text": "一。"}),
             ("say_to_user", {"text": "二。"}),
             ("say_to_user", {"text": "三。"})],
        )
        self.assertEqual("".join(item.text for item in settlement.streamed_speech), "一。二。三。")

    async def test_higher_say_waits_for_complete_lower_name_before_streaming(self) -> None:
        observed_before_name: list[tuple[str, dict]] = []

        async def chunks():
            yield {"tool_call": {"index": 0, "call_id": "read",
                                 "arguments_delta": '{"query":"x"}'}}
            yield delta(1, "say", "say_to_user", '{"text":"后说。"}')
            observed_before_name.extend(self.effects)
            yield {"tool_call": {"index": 0, "name": "web_search"}}

        settlement = await self.invoke(chunks())
        await asyncio.sleep(0)
        self.assertEqual(observed_before_name, [])
        self.assertEqual([name for name, _ in self.effects], ["say_to_user", "web_search"])
        self.assertEqual({item.code for item in settlement.receipts}, {"accepted", "detached"})

    async def test_known_say_length_overflow_never_reaches_executor(self) -> None:
        text = "先。" + "x" * 8_000
        settlement = await self.invoke([
            delta(0, "say", "say_to_user", '{"text":' + json.dumps(text) + '}'),
        ])
        self.assertEqual(settlement.receipts[0].code, "schema_invalid")
        self.assertEqual(settlement.streamed_speech, ())
        self.assertEqual(self.effects, [])

    async def test_streamed_clause_and_later_invalid_call_are_reported_as_separate_truths(self) -> None:
        async def chunks():
            yield delta(0, "say", "say_to_user", '{"text":"已拥有。')
            await asyncio.sleep(0)
            yield delta(0, "", "", '","extra":true}')

        settlement = await self.invoke(chunks())
        self.assertEqual(settlement.receipts[0].code, "schema_invalid")
        self.assertFalse(settlement.receipts[0].accepted)
        self.assertEqual(
            [(item.call_id, item.text) for item in settlement.streamed_speech],
            [("say", "已拥有。")],
        )
        self.assertEqual(self.effects, [("say_to_user", {"text": "已拥有。"})])

    async def test_invalid_suffix_after_streamed_say_blocks_every_non_say_call(self) -> None:
        async def chunks():
            yield delta(0, "say", "say_to_user", '{"text":"已拥有。')
            yield delta(1, "read", "web_search", '{"query":"must not run"}')
            yield delta(0, "", "", '","extra":true}')

        settlement = await self.invoke(chunks())
        codes = {receipt.call_id: receipt.code for receipt in settlement.receipts}
        self.assertEqual(codes, {"say": "schema_invalid", "read": "group_rejected"})
        self.assertEqual(self.effects, [("say_to_user", {"text": "已拥有。"})])

    async def test_second_say_never_streams_and_lowest_index_is_deterministic(self) -> None:
        settlement = await self.invoke([
            delta(1, "later", "say_to_user", '{"text":"不能说。"}'),
            delta(0, "first", "say_to_user", '{"text":"可以说。"}'),
        ])
        codes = {receipt.call_id: receipt.code for receipt in settlement.receipts}
        self.assertEqual(codes, {"first": "accepted", "later": "say_limit_exceeded"})
        self.assertEqual(self.effects, [("say_to_user", {"text": "可以说。"})])

    async def test_lower_say_reserves_streaming_before_its_first_stable_clause(self) -> None:
        effects_after_higher: list[tuple[str, dict]] = []

        async def chunks():
            yield delta(0, "lower", "say_to_user", '{"text":"等待尾部')
            yield delta(1, "higher", "say_to_user", '{"text":"不能抢先。"}')
            effects_after_higher.extend(self.effects)
            yield delta(0, "", "", '。"}')

        settlement = await self.invoke(chunks())
        codes = {receipt.call_id: receipt.code for receipt in settlement.receipts}
        self.assertEqual(effects_after_higher, [])
        self.assertEqual(codes, {"lower": "accepted", "higher": "say_limit_exceeded"})
        self.assertEqual(self.effects, [("say_to_user", {"text": "等待尾部。"})])

    async def test_early_say_requires_reserved_call_slot_and_stable_name(self) -> None:
        limited = self.harness(max_calls=1)
        over_limit = await self.invoke([
            delta(1, "say", "say_to_user", '{"text":"不能提前。"}'),
        ], harness=limited)
        self.assertEqual(over_limit.receipts[0].code, "call_limit_exceeded")
        self.assertEqual(self.effects, [])

        renamed = await self.invoke([
            delta(0, "renamed", "say_to_user", '{"text":"已拥有。'),
            delta(0, "", "web_search", '"}'),
        ])
        self.assertEqual(renamed.receipts[0].code, "conflicting_tool_name")
        self.assertEqual(renamed.streamed_speech[0].text, "已拥有。")

    async def test_observations_detach_but_control_effects_settle(self) -> None:
        release = asyncio.Event()

        async def slow(call):
            await release.wait()
            self.effects.append((call.name, dict(call.arguments)))

        executors = dict(self.executors)
        executors["web_search"] = slow
        harness = ToolHarness(ToolSurfacePolicy(), executors, state_provider=lambda: self.fence)
        settlement = await self.invoke(
            [delta(0, "read", "web_search", '{"query":"x"}')], harness=harness
        )
        self.assertTrue(settlement.settled)
        self.assertEqual(settlement.receipts[0].code, "detached")
        self.assertEqual(self.effects, [])
        release.set()
        await harness.wait_for_observations()
        self.assertEqual(self.effects, [("web_search", {"query": "x"})])

    async def test_observation_dedupe_and_outcomes_are_bounded_while_inflight_is_retained(self) -> None:
        release = asyncio.Event()

        async def execute(call):
            if call.arguments["query"] == "held":
                await release.wait()
            return {"accepted": True}

        executors = dict(self.executors)
        executors["web_search"] = execute
        harness = ToolHarness(
            ToolSurfacePolicy(), executors,
            observation_limiter=ObservationLimiter(3),
        )
        held = ToolCall(
            call_id="held", name="web_search", arguments={"query": "held"},
            index=0, raw_arguments='{"query":"held"}',
        )
        self.assertEqual(
            await harness._detach_observation(held, "invoke-held", "input-held"),
            "detached",
        )
        for index in range(270):
            call = ToolCall(
                call_id=f"call-{index}", name="web_search",
                arguments={"query": str(index)}, index=0,
                raw_arguments=json.dumps({"query": str(index)}),
            )
            self.assertEqual(
                await harness._detach_observation(
                    call, f"invoke-{index}", f"input-{index}"
                ),
                "detached",
            )
            for _ in range(20):
                if len(harness._observation_tasks) == 1:
                    break
                await asyncio.sleep(0)

        self.assertLessEqual(len(harness._observation_requests), 256)
        self.assertLessEqual(len(harness._observation_outcomes), 256)
        self.assertEqual(
            await harness._detach_observation(held, "invoke-late", "input-held"),
            "observation_reused",
        )
        release.set()
        await harness.wait_for_observations()

    async def test_detached_failures_are_retrieved_retained_and_forwarded(self) -> None:
        forwarded: list[ObservationOutcome] = []

        async def fail(call):
            raise RuntimeError("observation failed")

        executors = dict(self.executors)
        executors["web_search"] = fail
        harness = ToolHarness(
            ToolSurfacePolicy(), executors, state_provider=lambda: self.fence,
            observation_completion_hook=forwarded.append,
        )
        settlement = await self.invoke(
            [delta(0, "read", "web_search", '{"query":"x"}')], harness=harness
        )
        outcomes = await harness.wait_for_observations()
        self.assertEqual(settlement.receipts[0].code, "detached")
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].error_type, "RuntimeError")
        self.assertEqual(forwarded, list(outcomes))

    async def test_missing_observation_executor_is_rejected_instead_of_marked_detached(self) -> None:
        executors = dict(self.executors)
        del executors["web_search"]
        harness = ToolHarness(ToolSurfacePolicy(), executors, state_provider=lambda: self.fence)
        settlement = await self.invoke(
            [delta(0, "read", "web_search", '{"query":"x"}')], harness=harness
        )
        self.assertEqual(settlement.receipts[0].code, "executor_unavailable")
        self.assertFalse(settlement.receipts[0].accepted)

    async def test_ordinary_assistant_content_is_discarded_with_protocol_receipt(self) -> None:
        settlement = await self.invoke([
            {"choices": [{"delta": {"content": "this must stay hidden"}}]},
        ])
        self.assertTrue(settlement.assistant_content_discarded)
        self.assertEqual(settlement.receipts[0].code, "assistant_content_discarded")
        self.assertEqual(self.effects, [])

    async def test_cancellation_stops_new_effects_and_still_settles(self) -> None:
        harness = self.harness()

        async def chunks():
            yield delta(0, "read", "web_search", '{"query":"x"}')
            await asyncio.Event().wait()

        task = asyncio.create_task(self.invoke(chunks(), harness=harness))
        await asyncio.sleep(0)
        harness.cancel("invoke")
        settlement = await asyncio.wait_for(task, 1)
        self.assertTrue(settlement.cancelled)
        self.assertTrue(settlement.settled)
        self.assertEqual(self.effects, [])

    async def test_cancel_before_registration_tombstone_is_consumed_by_matching_run(self) -> None:
        harness = self.harness()
        harness.cancel("invoke")
        first = await self.invoke([
            delta(0, "task", "task_assign", '{"title":"t","prompt":"p","workspace_id":"repo"}'),
        ], harness=harness)
        second = await self.invoke([
            delta(0, "say", "say_to_user", '{"text":"now allowed。"}'),
        ], harness=harness)
        self.assertTrue(first.cancelled)
        self.assertFalse(second.cancelled)
        self.assertEqual(second.receipts[0].code, "accepted")
        self.assertEqual(self.effects, [("say_to_user", {"text": "now allowed。"})])

    async def test_cancel_tombstones_are_bounded_lru_entries(self) -> None:
        active = "a"
        harness = ToolHarness(
            ToolSurfacePolicy(), self.executors,
            state_provider=lambda: replace(self.fence, active_invoke_id=active),
            cancel_tombstone_limit=2,
        )
        surface = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        )
        harness.cancel("a")
        harness.cancel("b")
        harness.cancel("c")
        allowed = await harness.run(
            [delta(0, "say", "say_to_user", '{"text":"evicted。"}')],
            invoke_id="a", tool_surface=surface,
        )
        self.assertFalse(allowed.cancelled)
        self.assertEqual(allowed.receipts[0].code, "accepted")

    async def test_cancel_while_waiting_for_single_flight_is_preserved(self) -> None:
        harness = self.harness()
        release = asyncio.Event()

        async def first_chunks():
            await release.wait()
            if False:
                yield {}

        surface = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        )
        first = asyncio.create_task(harness.run(
            first_chunks(), invoke_id="invoke", tool_surface=surface,
        ))
        await asyncio.sleep(0)
        second = asyncio.create_task(harness.run(
            [delta(0, "say", "say_to_user", '{"text":"no"}')],
            invoke_id="waiting", tool_surface=surface,
        ))
        await asyncio.sleep(0)
        harness.cancel("waiting")
        release.set()
        await first
        settlement = await asyncio.wait_for(second, 1)
        self.assertTrue(settlement.cancelled)
        self.assertEqual(self.effects, [])

    async def test_external_cancel_while_waiting_cleans_registration_for_retry(self) -> None:
        active = "first"
        harness = ToolHarness(
            ToolSurfacePolicy(), self.executors,
            state_provider=lambda: replace(self.fence, active_invoke_id=active),
        )
        release = asyncio.Event()

        async def first_chunks():
            await release.wait()
            if False:
                yield {}

        surface = harness.policy.freeze_surface(
            InvokeMode.RESPOND, input_id="input", revision=2, context_version=8,
            response_grant=self.grant, current_response_grant=self.grant,
        )
        first = asyncio.create_task(harness.run(
            first_chunks(), invoke_id="first", tool_surface=surface,
        ))
        await asyncio.sleep(0)
        waiting = asyncio.create_task(harness.run(
            [], invoke_id="waiting", tool_surface=surface,
        ))
        await asyncio.sleep(0)
        waiting.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiting
        release.set()
        await first
        active = "waiting"
        retried = await harness.run([], invoke_id="waiting", tool_surface=surface)
        self.assertTrue(retried.settled)
        self.assertEqual(retried.receipts, ())

    async def test_external_cancel_while_waiting_provider_leaves_no_child_tasks(self) -> None:
        harness = self.harness()
        provider_started = asyncio.Event()
        provider_closed = asyncio.Event()

        async def chunks():
            try:
                provider_started.set()
                await asyncio.Event().wait()
                if False:
                    yield {}
            finally:
                provider_closed.set()

        baseline = asyncio.all_tasks()
        invoke = asyncio.create_task(self.invoke(chunks(), harness=harness))
        await asyncio.wait_for(provider_started.wait(), 1)
        invoke.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await invoke
        await asyncio.sleep(0)
        pending_children = [
            task for task in asyncio.all_tasks() - baseline
            if not task.done()
        ]
        self.assertTrue(provider_closed.is_set())
        self.assertEqual(pending_children, [])

    async def test_cancellation_preserves_speech_already_accepted_by_its_executor(self) -> None:
        harness = self.harness()

        async def chunks():
            yield delta(0, "say", "say_to_user", '{"text":"已接收。')
            await asyncio.Event().wait()

        task = asyncio.create_task(self.invoke(chunks(), harness=harness))
        for _ in range(10):
            if self.effects:
                break
            await asyncio.sleep(0)
        harness.cancel("invoke")
        settlement = await asyncio.wait_for(task, 1)
        self.assertTrue(settlement.cancelled)
        self.assertEqual(self.effects, [("say_to_user", {"text": "已接收。"})])
        self.assertEqual(settlement.receipts[0].code, "invoke_cancelled")
        self.assertEqual(settlement.streamed_speech[0].text, "已接收。")

    async def test_malformed_non_say_call_never_executes_and_silent_invoke_settles(self) -> None:
        malformed = await self.invoke([delta(0, "bad", "web_search", '{"query":')])
        silent = await self.invoke([])
        self.assertEqual(malformed.receipts[0].code, "malformed_arguments")
        self.assertTrue(silent.settled)
        self.assertEqual(silent.receipts, ())
        self.assertEqual(self.effects, [])


if __name__ == "__main__":
    unittest.main()
