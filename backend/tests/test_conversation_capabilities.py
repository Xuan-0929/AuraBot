"""Exercise shared conversation capabilities independently of pipeline turn grants.

Controlled memory I/O checks that stale prefetches never replace a newer query and
that frozen archives survive new input without clearing that input on completion.
SQLite remains real so tests cover canonical delivery and archive boundaries.
"""

import asyncio
import tempfile
import unittest
from pathlib import Path

from backend.main_agent.capabilities import ConversationCapabilities, ConversationMemory
from backend.main_agent.context import ContextStore
from backend.main_agent.tool_runtime import ToolCall, ToolSurfacePolicy


class Memory:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.batches = []
        self.queries = []

    async def recall_context_async(self, *, query, **kwargs):
        self.queries.append(query)
        if query == "old":
            await self.release.wait()
        return {"text": query, "memory_ids": [query]}

    async def remember_conversation(self, **batch):
        self.batches.append(batch)
        self.started.set()
        await self.release.wait()
        return {"summary": "archived"}


class ConversationMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.context = ContextStore(Path(self.directory.name) / "context.sqlite3")
        self.addCleanup(self.context.close)
        self.service = Memory()
        self.memory = ConversationMemory(self.context, self.service, "profile-a", idle_seconds=0.01)
        self.addAsyncCleanup(self.memory.shutdown)

    async def test_late_prefetch_cannot_replace_new_query(self):
        self.memory.prefetch("old")
        await asyncio.sleep(0)
        self.memory.prefetch("new")
        await asyncio.sleep(0)
        self.service.release.set()
        await asyncio.sleep(0)
        self.assertEqual((await self.memory.recall("new"))["text"], "new")
        self.assertEqual(self.service.queries.count("new"), 1)

    async def test_new_input_cancels_idle_but_not_frozen_archive(self):
        self.context.append_event("user_segment", {"segment_id": "old", "text": "old"})
        self.memory.confirm_activity()
        await asyncio.wait_for(self.service.started.wait(), 1)
        self.memory.activity_started()
        self.context.append_event("user_segment", {"segment_id": "new", "text": "new"})
        self.service.release.set()
        async with asyncio.timeout(1):
            while self.context.pending_conversation_archive() is not None:
                await asyncio.sleep(0)
        self.assertEqual([t["content"] for t in self.context.fold().conversation_turns], ["new"])
        self.assertEqual(self.service.batches[0]["profile_id"], "profile-a")

    async def test_tool_duplicate_waiters_share_execution_and_keep_profile_scope(self):
        calls = []
        async def search(call):
            calls.append(call.arguments)
            await self.service.release.wait()
            return {"sources": ["source-1"]}
        capabilities = ConversationCapabilities(self.context, self.memory,
            ToolSurfacePolicy(observation_tools=("web_search",)), {"web_search": search})
        self.addAsyncCleanup(capabilities.shutdown)
        call = ToolCall("call-1", "web_search", {"query": "weather"})
        first = asyncio.create_task(capabilities.execute("session-1", call))
        second = asyncio.create_task(capabilities.execute("session-1", call))
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        self.service.release.set()
        self.assertEqual(await second, {"sources": ["source-1"]})
        self.assertEqual(calls, [{"query": "weather"}])
        self.assertEqual(await capabilities.execute("session-1", call), {"sources": ["source-1"]})
