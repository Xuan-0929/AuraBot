# Memory Owner Cutover Design

## Goal

Make the Python Main Agent and its SQLite/Qdrant Memory bridge the only conversation-memory owner used by AuraBot's normal Electron text-chat and Memory-management surfaces.

## Chosen approach

Electron Main will own a persistent loopback `aurabot.main-agent.v1` realtime connection. It obtains a one-shot token, opens a text-mode session, submits `text.submit`, projects `clause.offer` messages into the existing Electron message repository and Renderer broadcasts, and acknowledges delivered clauses with `renderer.ack`.

This is preferred over a Renderer-owned WebSocket because credentials and protocol state stay outside the renderer, and over a new HTTP chat endpoint because the Backend already has a tested realtime contract with ordering, renderer leases, delivery ACKs and future voice reuse.

## Ownership and data flow

1. Renderer sends the existing typed `chat:send-message` IPC request.
2. Electron Main creates the user message as a local UI projection and submits the text to the Python realtime client.
3. Python `MainAgentRuntime` records canonical Context, performs automatic Memory recall, and produces clauses.
4. Electron Main creates or appends the projected assistant message, broadcasts updates, and ACKs each delivered clause.
5. After five minutes of confirmed-user-input idleness, Python seals and processes the durable episode. The TypeScript Memory extractor/service is not invoked.

Electron message rows remain presentation history for this milestone. Canonical conversation reasoning and long-term Memory live in the Python profile stores.

## Memory management surface

The Electron Backend client will consume:

- `GET /api/v1/readiness`
- `GET /api/v1/memories`
- `POST /api/v1/memories/forget`
- `POST /api/v1/memory-jobs/{jobId}/retry`

Memory Debug will display active memories and extraction/vector job summaries from these routes. It will allow explicit forget and retry actions. It will no longer label legacy `memory_nodes`, edges or summaries as the active Python Memory state.

## Failure and rollback behavior

- Backend connection/token/session failures return a stable unavailable result to the user and leave Python Memory untouched.
- The desktop never falls back to `ChatOrchestrator` or `MemoryService` for the same input.
- Pending local projection rows are reconciled to an error response instead of being written into old Memory.
- Existing legacy TypeScript Memory tables remain intact and read-only during the cutover milestone. Rollback selects the old application build, not a runtime dual-write mode.

## Scope boundaries

Included:

- Electron Main realtime text client.
- Existing chat IPC projection over the new client.
- New Memory browse/forget/retry desktop client and settings presentation.
- Disabling legacy proactive Memory follow-up mutation in the new ownership mode.
- Contract, unit, integration and end-to-end tests for the cutover.

Excluded:

- Semantic embedding provider selection or index migration.
- Model-backed extraction and consolidation scheduling.
- Legacy graph-data import.
- Task UI, Artifact UI, production Node/Pi packaging and speech enablement.

## Acceptance criteria

- Normal Electron text input reaches `MainAgentRuntime` and does not call legacy TypeScript Memory.
- Each offered clause is projected once and acknowledged only after local delivery succeeds.
- Backend failure is explicit and never triggers legacy Memory fallback.
- Memory Debug reads Python active memories/jobs and supports forget/retry.
- Forget immediately removes a memory from same-revision recall through SQLite authority.
- Existing legacy Memory data remains present but receives no chat/debug/proactive mutation.
- Backend focused/full tests, frontend unit/integration tests, typecheck and build pass.
