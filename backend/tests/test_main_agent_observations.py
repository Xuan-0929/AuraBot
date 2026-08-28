"""Protects Observation identity, Tavily's search/extract contract, and workspace trust boundaries.

Tests use the public async executors and filesystem results. Removed DNS-pinned HTTP parsing, deadline thread
pools, Harness-owned dedupe caches, and limiter internals are deliberately absent because Tavily now performs
public-page extraction and Runtime owns one canonical Observation registry.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from backend.main_agent.observations import (
    ObservationLedger,
    ObservationSecurityError,
    TavilyTools,
    WorkspaceReader,
)
from backend.main_agent.settings import WorkspaceRegistry
from backend.main_agent.tool_runtime import ToolCall


class FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("provider detail must not escape")

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


class ObservationLedgerTests(unittest.TestCase):
    def test_same_input_arguments_reuse_one_identity_and_terminal_settlement_is_idempotent(self) -> None:
        ledger = ObservationLedger()
        first = ledger.start("o1", input_id="i1", revision=1, kind="web_search", arguments={"query": "Aura"})
        reused = ledger.start("o2", input_id="i1", revision=2, kind="web_search", arguments={"query": "Aura"})
        terminal, changed = ledger.settle("o1", "completed", {"ok": True})
        again, duplicate = ledger.settle("o1", "failed", "Error")

        self.assertEqual(first, ("o1", True))
        self.assertEqual(reused, ("o1", False))
        self.assertTrue(changed)
        self.assertFalse(duplicate)
        self.assertEqual(terminal, again)


class TavilyToolsTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_registers_bounded_sources_and_read_uses_managed_extract(self) -> None:
        client = FakeClient([
            FakeResponse({"results": [{"title": "Aura", "url": "https://example.com/a", "content": "snippet"}]}),
            FakeResponse({"results": [{"url": "https://example.com/a", "raw_content": "# title\n" + "x" * 70_000}]}),
        ])
        tools = TavilyTools("secret", client=client, max_sources=8)
        search = await tools.search(ToolCall("s", "web_search", {"query": "Aura"}))
        source_id = search["results"][0]["source_id"]
        read = await tools.read(ToolCall("r", "web_read", {"source_id": source_id}))

        self.assertNotIn("https://", source_id)
        self.assertEqual(client.requests[0][0], "https://api.tavily.com/search")
        self.assertEqual(client.requests[0][1]["json"]["query"], "Aura")
        self.assertEqual(client.requests[1][0], "https://api.tavily.com/extract")
        self.assertEqual(client.requests[1][1]["json"], {
            "urls": ["https://example.com/a"],
            "extract_depth": "basic",
            "format": "markdown",
            "include_images": False,
        })
        self.assertLessEqual(len(read["content"].encode()), 65_536)
        await tools.aclose()
        self.assertTrue(client.closed)

    async def test_read_rejects_unknown_source_without_provider_request(self) -> None:
        client = FakeClient([])
        tools = TavilyTools("secret", client=client)
        with self.assertRaises(ObservationSecurityError):
            await tools.read(ToolCall("r", "web_read", {"source_id": "unknown"}))
        self.assertEqual(client.requests, [])


class WorkspaceReaderTests(unittest.TestCase):
    def test_list_and_page_read_hide_secrets_and_stay_under_registered_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "safe.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
            (root / ".env").write_text("SECRET=yes", encoding="utf-8")
            reader = WorkspaceReader(WorkspaceRegistry({"repo": root}))

            listing = reader.list("repo")
            page = reader.read("repo", "safe.txt", start_line=2, max_lines=1)
            self.assertEqual([item["name"] for item in listing["items"]], ["safe.txt"])
            self.assertEqual(page["content"], "two\n")
            self.assertEqual(page["next_start_line"], 3)
            with self.assertRaises(ObservationSecurityError):
                reader.read("repo", "../outside")

    def test_symlink_binary_and_secret_paths_are_denied(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "binary").write_bytes(b"a\0b")
            (root / "secret.key").write_text("secret", encoding="utf-8")
            (root / "target").write_text("target", encoding="utf-8")
            (root / "link").symlink_to(root / "target")
            reader = WorkspaceReader(WorkspaceRegistry({"repo": root}))
            for path in ("binary", "secret.key", "link"):
                with self.assertRaises(ObservationSecurityError):
                    reader.read("repo", path)
