# Memory Owner Cutover Implementation Plan

> **For Codex:** Execute each task test-first. Keep Python Main Agent/Memory as the sole semantic owner; Electron message rows are presentation projections only.

**Goal:** Route normal Electron text chat and Memory management through the Python `MainAgentRuntime` and `MemoryBridge`, with explicit failure and no legacy TypeScript Memory fallback.

**Architecture:** Electron Main owns one reconnectable text-only realtime client. A small chat projection service persists existing UI messages, folds Backend clauses by `input_id`, broadcasts only after persistence, and then acknowledges delivery. The Backend gateway client owns typed Memory browse/forget/retry HTTP calls. Legacy TypeScript Memory tables remain intact but normal chat, Memory Debug and proactive scheduling stop mutating them.

**Tech Stack:** Electron 41, TypeScript 6, Vue 3, Pinia, Vitest, FastAPI realtime/HTTP gateway, SQLite, local Qdrant.

---

## Task 1: Production realtime text client

**Files:**

- Create: `frontend/src/main/mainAgentRealtimeClient.ts`
- Create: `frontend/tests/unit/main/mainAgentRealtimeClient.spec.ts`

1. Write failing tests for token acquisition, required WebSocket subprotocols, `session.open`, monotonic sequence/UUID commands, accepted `text.submit`, clause delivery callback, post-delivery `renderer.ack`, duplicate clause suppression, rejected command propagation, and close/reconnect.
2. Run the single test file and confirm it fails because the production client does not exist.
3. Implement the minimum dependency-injected client using `fetch`, `WebSocket`, a UUID factory and bounded command timeouts. Keep one connection; no microphone, TTS or diagnostic fault machinery.
4. Re-run the single test file until green.
5. Commit only the client and its test as `feat(main): add production main-agent realtime client`.

## Task 2: Main Agent chat projection

**Files:**

- Create: `frontend/src/main/mainAgentChatProjection.ts`
- Create: `frontend/tests/unit/main/mainAgentChatProjection.spec.ts`
- Modify: `frontend/src/main/registerIpc.ts`
- Modify: `frontend/src/main/index.ts`

1. Write failing projection tests for local user-message persistence, `text.submit`, clause grouping by `input_id`, append-in-place broadcasts, ACK only after successful persistence/broadcast, and explicit unavailable assistant response without legacy service calls.
2. Run the new test and confirm red.
3. Implement the projection with the existing `ProfileRepository`, `SessionRepository`, `MessageRepository`, and typed broadcasts. Preserve the public `SendChatMessageResult` contract.
4. Replace `chat:send-message` ownership in `registerIpc` with an injected handler; construct/start/close the new client in Electron Main. Do not call `ChatOrchestrator.sendMessage` from normal IPC.
5. Re-run projection, Renderer store, IPC contract and typecheck tests.
6. Commit scoped files as `feat(chat): route desktop text through main agent`.

## Task 3: Typed Python Memory gateway client

**Files:**

- Modify: `frontend/src/shared/contracts/backend.ts`
- Modify: `frontend/src/main/backendGatewayClient.ts`
- Modify: `frontend/tests/unit/main/backendGatewayClient.spec.ts`

1. Add failing tests for active Memory browse, forget request/response, job retry and stable error parsing.
2. Confirm the client tests fail on missing methods.
3. Add minimal typed DTOs and fetch methods for `GET /api/v1/memories`, `POST /api/v1/memories/forget`, and `POST /api/v1/memory-jobs/{jobId}/retry` while retaining health/readiness behavior.
4. Re-run focused tests and typecheck.
5. Commit as `feat(memory): add desktop python memory client`.

## Task 4: Memory management IPC and Renderer state

**Files:**

- Modify: `frontend/src/shared/contracts/ipc.ts`
- Modify: `frontend/src/preload/index.ts`
- Modify: `frontend/src/main/registerIpc.ts`
- Modify: `frontend/src/app/store.ts`
- Modify: `frontend/tests/unit/renderer/store.spec.ts`
- Create or modify focused preload/IPC contract tests as needed.

1. Write failing tests that Memory Debug loads Python Memory data and that forget/retry actions call the new typed channels then refresh state.
2. Confirm red on missing IPC/API methods.
3. Add `memory:forget` and `memory:retry-job` channels and expose typed preload methods. Change the debug snapshot source from `MemoryService.retrieveForProfile` to `BackendGatewayClient`.
4. Implement Pinia actions with explicit loading/error state and refresh-after-mutation.
5. Re-run store, preload/IPC and backend client tests.
6. Commit as `feat(memory): connect management ipc to python owner`.

## Task 5: Memory Debug presentation cutover

**Files:**

- Modify: `frontend/src/features/settings/pages/MemoryDebugSettingsPage.vue`
- Modify: `frontend/src/features/memory-debug/MemoryDebugContent.vue`
- Modify or create component tests under `frontend/tests/unit/renderer/`.

1. Write failing component tests for Python active Memory/job rendering, forget action, retryable failed jobs, empty state and Backend-unavailable state.
2. Confirm red against the legacy graph UI.
3. Replace legacy nodes/edges/summaries presentation with compact active Memory and job sections. Preserve functional styling and accessible action labels.
4. Re-run component tests, typecheck and build.
5. Commit as `feat(memory): show python memory management state`.

## Task 6: Freeze legacy proactive Memory mutation

**Files:**

- Modify: `frontend/src/core/proactive/proactiveTriggerEngine.ts`
- Modify: `frontend/tests/unit/core/proactive/proactiveTriggerEngine.spec.ts`
- Modify: `frontend/src/main/index.ts`

1. Add a failing test proving `memoryFollowUpsEnabled: false` skips both retrieval and completion while idle nudge remains active.
2. Confirm red.
3. Add the narrow option and wire production to false. Do not delete legacy tables/services and do not disable idle nudge.
4. Re-run proactive tests and search production paths for remaining normal `memoryService` mutations.
5. Commit as `refactor(memory): freeze legacy proactive writes`.

## Task 7: Ownership integration and final verification

**Files:**

- Modify tests/docs only where an ownership regression needs coverage or handoff notes.

1. Add an integration test proving normal `chat:send-message` uses the injected Main Agent handler and never calls the legacy `ChatOrchestrator`/`MemoryService` path.
2. Run focused ownership tests.
3. Run all 523+ Backend tests with pinned Node 22/Pi Runtime.
4. Rebuild `better-sqlite3` for Node 22, then run all frontend unit/integration tests, typecheck and build.
5. Run repository searches for `chatOrchestrator.sendMessage`, active Memory Debug legacy reads and proactive Memory completion in production wiring; classify any remaining reference.
6. Review `git diff --check`, staged scope and branch history. Preserve all unrelated main-checkout files.
7. Commit final test/docs adjustments as `test(memory): verify python ownership cutover`.
