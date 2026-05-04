# AuraBot MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable AuraBot desktop MVP that validates three core hypotheses: natural chat timing beats fixed timeout, mixed memory beats flat memory, and a chat-first companion can still control background tasks without losing “alive” feeling.

**Architecture:** Use an Electron desktop shell with a React renderer, a Node/TypeScript orchestration core, and a local SQLite store. Keep one user-facing main chat session per active profile, route background work into task sessions with capped depth, and separate lightweight gate/classification logic from main response generation to reduce cost and context pollution.

**Tech Stack:** Electron, React, TypeScript, Vite, Zustand, better-sqlite3, Zod, p-queue, Vitest, Playwright

---

## 1. This Plan Covers

This document is the execution-facing breakdown of the PRD at [AuraBot 产品需求文档](/Users/xuan/Documents/New%20project%204/docs/aurabot-product-requirements.md).

It does not try to build the entire AuraBot vision in one pass. It narrows the first implementation to a testable MVP plus the minimum scaffolding needed not to paint the codebase into a corner.

## 2. MVP Boundary

### 2.1 Must Ship in MVP

1. Desktop chat shell with one active AuraBot profile.
2. Soul/persona config loading for that profile.
3. Main chat session lifecycle and message persistence.
4. Lightweight gate/classifier that decides:
   - whether the user has likely finished speaking
   - whether the input is chat or task
   - whether memory writeback should be deferred
5. Main response engine that can read context and produce replies.
6. Mixed memory v1:
   - time-driven memory
   - non-time-driven memory
   - summary + relation-based lookup
7. Task delegation v1:
   - create task from main chat
   - view task status from main UI
   - interrupt or revise task from main UI
8. Local event logging and basic observability for debugging response timing and memory writes.

### 2.2 Can Land Right After MVP

1. Multi-profile support beyond a single active profile.
2. Scheduled jobs and reminders.
3. Desktop notifications for proactive follow-ups.
4. External channel adapters such as QQ and WeChat.

### 2.3 Explicitly Out of Scope for MVP

1. Game integration.
2. Live2D polish beyond a placeholder surface.
3. Profile-to-profile interaction.
4. Unlimited recursive task spawning.
5. Full-blown plugin marketplace.

## 3. Product-to-Engineering Translation

The PRD contains several big ideas. For implementation, each one should be translated into a narrower technical target:

| PRD concept | Engineering target in MVP |
| --- | --- |
| 活人感 | Avoid fixed-timeout-only reply logic and preserve message cadence/context |
| 图状记忆 | Store memory nodes plus explicit relation edges; support bounded graph traversal |
| 分层记忆 | Keep a compact summary layer before loading detailed nodes |
| 聊天优先 | Reserve a single main chat session as the user-facing control plane |
| 助手能力 | Spawn task sessions that report back into the main session |
| 可控子任务 | Main session can inspect, interrupt, or revise child tasks |
| 阶段性交付 | Ship chat core before external channels and ecosystem integrations |

## 4. Recommended Technical Strategy

### 4.1 Desktop Shell

Use `Electron + React + TypeScript` for the first build.

Why this stack:

1. Desktop companion products benefit from long-running background processes, local notifications, and easy child-process/tool integration.
2. Electron gives a straightforward bridge to Node-based tooling, model adapters, filesystem access, and future external channel adapters.
3. The product is interaction-heavy and orchestration-heavy; packaging efficiency matters less in MVP than developer speed and integration flexibility.

### 4.2 Runtime Split

Use a four-way split:

1. `renderer`
   - UI only
   - sends typed commands
   - renders chat, task list, status, settings
2. `main`
   - Electron app lifecycle
   - IPC registration
   - notifications
   - app window management
3. `core`
   - chat orchestration
   - gate logic
   - memory engine
   - task engine
   - schedule engine
4. `shared`
   - typed contracts
   - schemas
   - enums
   - DTOs

### 4.3 Data Storage

Use local SQLite for MVP.

Reasons:

1. Local-first companion products need durable personal state.
2. The MVP needs relational integrity for sessions, tasks, memory nodes, and edges.
3. SQLite is enough for a single-user desktop app and keeps setup simple.

### 4.4 Model Adapter Strategy

Do not hard-wire one single model call path. Create two logical model roles from day one:

1. `gateModel`
   - cheap
   - fast
   - handles timing and classification decisions
2. `chatModel`
   - more capable
   - handles final user-facing replies and memory-aware generation

Even if both point to the same provider at first, keep them separate in config and code.

### 4.5 Memory Strategy in MVP

Do not attempt a full open-ended knowledge graph on day one.

Implement a constrained hybrid:

1. `memory_summary`
   - compact topical summaries per profile
2. `memory_nodes`
   - atomic facts, preferences, events, traits
3. `memory_edges`
   - relation links between nodes
4. `memory_state`
   - active, stale, completed, archived

This lets us validate whether “relation-aware recall” actually helps before building a more ambitious graph system.

### 4.6 Session and Task Strategy

Adopt one permanent user-facing chat session plus spawned task sessions.

Rules:

1. One active main chat session per profile.
2. Task sessions are children of the main session.
3. Child sessions can spawn one more level if necessary.
4. Maximum task depth is `3`.
5. Memory writes from child sessions must go through a serialized memory write queue.

## 5. Proposed Repository Layout

```text
AuraBot/
  package.json
  pnpm-lock.yaml
  electron.vite.config.ts
  tsconfig.json
  playwright.config.ts
  vitest.config.ts
  docs/
    superpowers/
      specs/
      plans/
  scripts/
    dev.ts
    seed-profile.ts
    inspect-db.ts
  src/
    main/
      index.ts
      createWindow.ts
      registerIpc.ts
      notifications.ts
    renderer/
      index.html
      src/
        main.tsx
        App.tsx
        app/
          store.ts
          routes.tsx
        features/
          chat/
            ChatWindow.tsx
            MessageList.tsx
            Composer.tsx
            useChatController.ts
          tasks/
            TaskPanel.tsx
            TaskDetail.tsx
          profiles/
            ProfileSwitcher.tsx
          settings/
            SettingsPanel.tsx
          status/
            AgentStatusBar.tsx
        shared-ui/
          EmptyState.tsx
          LoadingDots.tsx
    shared/
      contracts/
        ipc.ts
        chat.ts
        tasks.ts
        profiles.ts
      schemas/
        profile.ts
        message.ts
        task.ts
        memory.ts
        config.ts
      constants/
        session.ts
        memory.ts
    core/
      bootstrap/
        loadConfig.ts
        createServices.ts
      llm/
        provider.ts
        gateModel.ts
        chatModel.ts
      orchestration/
        chatOrchestrator.ts
        gateEvaluator.ts
        responsePlanner.ts
        eventBus.ts
      memory/
        memoryService.ts
        memoryWriter.ts
        memoryRetriever.ts
        memorySummaryService.ts
        graphTraversal.ts
      tasks/
        taskService.ts
        taskRunner.ts
        taskRegistry.ts
        taskInterruption.ts
      schedules/
        scheduleService.ts
      profiles/
        profileService.ts
        soulService.ts
      storage/
        db.ts
        migrations.ts
        repositories/
          messageRepository.ts
          sessionRepository.ts
          taskRepository.ts
          memoryRepository.ts
          profileRepository.ts
      telemetry/
        logger.ts
        eventLogService.ts
  tests/
    unit/
      core/
      shared/
    integration/
      orchestration/
      memory/
      tasks/
    e2e/
      chat-flow.spec.ts
      task-flow.spec.ts
```

## 6. Milestones

### Milestone A: Skeleton and Local Runtime

**Outcome:** The app boots locally, persists state, and has typed boundaries between UI and orchestration.

**Success criteria:**

1. Desktop app opens a main window.
2. Renderer can send a typed message to the main process.
3. SQLite database initializes with migrations.
4. Test tooling runs.

### Milestone B: Chat Core

**Outcome:** The user can chat with a single AuraBot profile and see persisted messages and agent status.

**Success criteria:**

1. Messages are stored and replayed on app refresh.
2. AuraBot has a visible state: idle, listening, thinking, replying.
3. Soul config is loaded and used in reply generation.

### Milestone C: Gate and Timing

**Outcome:** The app no longer replies on a naive “every message immediately triggers a final answer” loop.

**Success criteria:**

1. Consecutive short user messages can be coalesced into one evaluation window.
2. Gate logic produces a traceable reason for reply/no-reply decisions.
3. User interrupt during generation is supported.

### Milestone D: Mixed Memory

**Outcome:** AuraBot stores and retrieves both stable traits and time-driven events, and can recall them during response generation.

**Success criteria:**

1. Memory nodes persist in SQLite.
2. Event-type memories can be revisited later.
3. Retrieval can return summary plus related nodes.

### Milestone E: Task Delegation

**Outcome:** AuraBot can create task sessions from chat, show task status, and accept user interruption or revision.

**Success criteria:**

1. Task creation works from the main chat.
2. Task sessions are visible in the UI.
3. User can cancel or revise a running task.

### Milestone F: Hardening

**Outcome:** Core flows are testable, inspectable, and ready for phase-two features.

**Success criteria:**

1. Basic e2e flows pass.
2. Logs show gate decisions, memory writes, and task lifecycle changes.
3. The codebase is ready for multi-profile and schedules without major rewrites.

## 7. MVP Feature List by Priority

### P0

1. Desktop app shell
2. One profile
3. Soul loading
4. Main chat session
5. Gate/classifier
6. Chat response generation
7. Message persistence
8. Mixed memory v1
9. Task spawn from chat
10. Task status panel
11. Task interrupt/revise
12. Debug event log

### P1

1. Multiple profiles
2. Scheduled reminders
3. Proactive notifications
4. External tool registry
5. Better memory scoring and pruning

### P2

1. QQ/微信 adapters
2. Live2D polish
3. Game adapters
4. Rich plugin ecosystem

## 8. Key Technical Decisions

### 8.1 One Main Chat Session, Not Many Peer Sessions

Why:

1. The PRD treats the main user conversation as the emotional center of the product.
2. A single main session keeps identity, continuity, and user mental model clean.
3. Tasks can still branch without turning the UI into a generic agent console.

### 8.2 Task Sessions Are Operable from the Main Session

Why:

1. If AuraBot is both companion and assistant, the user must be able to ask “做到哪了” in the main UI.
2. This does not require exposing raw child-session internals in MVP.
3. A task summary and interrupt/revise controls are enough for the first build.

### 8.3 Memory Writes Must Be Serialized

Why:

1. The PRD explicitly worries about concurrency and “谁写进去算谁的”.
2. Serialized writes are simpler and safer than optimistic concurrent graph mutation in MVP.
3. We can revisit finer-grained locking only after proving load pressure exists.

### 8.4 Graph Memory Should Start Constrained

Why:

1. Full graph ambition is attractive but expensive.
2. MVP only needs enough relation structure to test whether linked recall feels more alive.
3. Constrained edge types are easier to debug than free-form graph generation.

Suggested edge types:

1. `relates_to`
2. `caused_by`
3. `scheduled_for`
4. `preference_for`
5. `opposes`

## 9. Suggested Database Tables

### Core identity and config

1. `profiles`
2. `souls`
3. `app_settings`

### Chat and session

1. `sessions`
2. `messages`
3. `session_links`

### Memory

1. `memory_nodes`
2. `memory_edges`
3. `memory_summaries`
4. `memory_write_queue`

### Tasking

1. `tasks`
2. `task_events`
3. `task_artifacts`

### Telemetry

1. `event_logs`

## 10. UI Surfaces Required in MVP

### 10.1 Main Chat Window

Must show:

1. Message stream
2. Input box
3. Send action
4. Agent status
5. Lightweight memory-aware or task-aware system hints when useful

### 10.2 Task Panel

Must show:

1. Task title
2. Status: queued, running, waiting, completed, failed, interrupted
3. Short latest progress text
4. Interrupt action
5. Revise action

### 10.3 Settings Surface

Must show:

1. Active profile
2. Model config
3. Gate model config
4. Storage location
5. Debug logging toggle

## 11. Test Strategy

### Unit tests

1. Gate evaluator rules and output schema
2. Memory classification and write queue behavior
3. Graph traversal bounds
4. Task status transitions

### Integration tests

1. Chat orchestration with persisted sessions
2. Message -> gate -> response pipeline
3. Message -> task classification -> task creation pipeline
4. Event memory follow-up retrieval

### End-to-end tests

1. User sends several fragmented messages and gets one natural reply
2. User mentions a future event and AuraBot can later recall it
3. User assigns a task and then interrupts it from the main UI

## 12. Recommended Build Order

### Task 1: Bootstrap the Desktop Workspace

**Files:**
- Create: `package.json`
- Create: `electron.vite.config.ts`
- Create: `tsconfig.json`
- Create: `src/main/index.ts`
- Create: `src/renderer/src/main.tsx`
- Create: `src/shared/contracts/ipc.ts`
- Test: `tests/e2e/app-launch.spec.ts`

- [ ] Initialize an Electron + React + TypeScript workspace with `pnpm`.
- [ ] Add a single empty main window and confirm the renderer mounts.
- [ ] Add typed IPC contracts before adding any feature-specific handlers.
- [ ] Wire Vitest and Playwright so the workspace has both unit and e2e test entry points.
- [ ] Commit the bootstrap once the app launches and tests can be invoked.

### Task 2: Add Local Storage and Repositories

**Files:**
- Create: `src/core/storage/db.ts`
- Create: `src/core/storage/migrations.ts`
- Create: `src/core/storage/repositories/profileRepository.ts`
- Create: `src/core/storage/repositories/sessionRepository.ts`
- Create: `src/core/storage/repositories/messageRepository.ts`
- Test: `tests/integration/storage/db-init.spec.ts`

- [ ] Add SQLite bootstrap and migration support.
- [ ] Define the first schema for profiles, sessions, and messages.
- [ ] Create repository wrappers instead of sprinkling SQL through orchestrators.
- [ ] Add one integration test that boots an empty DB and verifies required tables exist.
- [ ] Commit the storage foundation before moving into chat logic.

### Task 3: Build the Main Chat UI Shell

**Files:**
- Create: `src/renderer/src/features/chat/ChatWindow.tsx`
- Create: `src/renderer/src/features/chat/MessageList.tsx`
- Create: `src/renderer/src/features/chat/Composer.tsx`
- Create: `src/renderer/src/features/status/AgentStatusBar.tsx`
- Test: `tests/e2e/chat-shell.spec.ts`

- [ ] Create a three-part chat shell: message list, composer, status bar.
- [ ] Persist sent messages through IPC instead of local component state only.
- [ ] Show agent status as part of the shell from day one.
- [ ] Add one e2e test that types a message and confirms it appears after reload.
- [ ] Commit the UI shell as the first user-visible milestone.

### Task 4: Add Profile and Soul Loading

**Files:**
- Create: `src/core/profiles/profileService.ts`
- Create: `src/core/profiles/soulService.ts`
- Create: `src/shared/schemas/profile.ts`
- Create: `src/shared/schemas/config.ts`
- Test: `tests/unit/core/profiles/soulService.spec.ts`

- [ ] Define the profile and soul schema using Zod.
- [ ] Add one default profile seed so the app works without manual setup.
- [ ] Load soul content into the chat orchestration context.
- [ ] Add a unit test that rejects malformed soul config and accepts valid config.
- [ ] Commit once the app can boot with a stable default persona.

### Task 5: Implement the Gate Evaluator

**Files:**
- Create: `src/core/orchestration/gateEvaluator.ts`
- Create: `src/core/llm/gateModel.ts`
- Create: `src/shared/contracts/chat.ts`
- Test: `tests/unit/core/orchestration/gateEvaluator.spec.ts`

- [ ] Define a typed gate output contract with fields for reply decision, task classification, and confidence.
- [ ] Implement the first gate pass with a provider-backed model adapter plus a deterministic fallback rule path.
- [ ] Record the reason for each gate decision in event logs for debugging.
- [ ] Add tests covering fragmented messages, immediate replies, and clear task requests.
- [ ] Commit once gate output is stable and inspectable.

### Task 6: Implement the Main Chat Orchestrator

**Files:**
- Create: `src/core/orchestration/chatOrchestrator.ts`
- Create: `src/core/llm/chatModel.ts`
- Create: `src/core/orchestration/responsePlanner.ts`
- Test: `tests/integration/orchestration/chat-pipeline.spec.ts`

- [ ] Build a pipeline from user message -> context assembly -> model reply -> persistence.
- [ ] Keep gate evaluation and final response generation in separate code paths.
- [ ] Support generation cancellation when the user sends a new message mid-reply.
- [ ] Add one integration test for multi-message coalescing and one for interruption.
- [ ] Commit when the app can hold a stable, persisted conversation.

### Task 7: Add Mixed Memory v1

**Files:**
- Create: `src/core/memory/memoryService.ts`
- Create: `src/core/memory/memoryWriter.ts`
- Create: `src/core/memory/memoryRetriever.ts`
- Create: `src/core/memory/memorySummaryService.ts`
- Create: `src/core/memory/graphTraversal.ts`
- Test: `tests/integration/memory/memory-roundtrip.spec.ts`

- [ ] Define memory node types for `trait`, `preference`, `event`, and `follow_up`.
- [ ] Add a serialized write queue for memory writes.
- [ ] Implement summary-first retrieval plus bounded related-node expansion.
- [ ] Add one integration test where an event memory is stored and later recalled.
- [ ] Commit only after retrieval traces are visible in logs.

### Task 8: Add the Task Engine and Task Panel

**Files:**
- Create: `src/core/tasks/taskService.ts`
- Create: `src/core/tasks/taskRunner.ts`
- Create: `src/core/tasks/taskRegistry.ts`
- Create: `src/core/tasks/taskInterruption.ts`
- Create: `src/renderer/src/features/tasks/TaskPanel.tsx`
- Create: `src/renderer/src/features/tasks/TaskDetail.tsx`
- Test: `tests/integration/tasks/task-lifecycle.spec.ts`

- [ ] Add task creation from gate-classified task requests.
- [ ] Create child task sessions linked to the main session.
- [ ] Show task progress in the renderer with minimal but clear lifecycle states.
- [ ] Add interrupt and revise controls from the main UI.
- [ ] Commit when one real task flow can run end-to-end.

### Task 9: Add Debugging and Event Log Surfaces

**Files:**
- Create: `src/core/telemetry/logger.ts`
- Create: `src/core/telemetry/eventLogService.ts`
- Create: `src/renderer/src/features/settings/SettingsPanel.tsx`
- Test: `tests/integration/orchestration/event-log.spec.ts`

- [ ] Log gate decisions, task lifecycle changes, memory writes, and cancellations.
- [ ] Add a developer toggle to expose or hide debug traces.
- [ ] Ensure logs never become the only source of user-facing state.
- [ ] Add one test that verifies key orchestration events are written during a chat + task flow.
- [ ] Commit the observability layer before phase-two features.

### Task 10: Hardening Pass and MVP Demo Flow

**Files:**
- Modify: `tests/e2e/chat-flow.spec.ts`
- Modify: `tests/e2e/task-flow.spec.ts`
- Modify: `README.md`
- Modify: `docs/aurabot-product-requirements.md`

- [ ] Add one demo-ready chat flow covering fragmented messages and natural reply timing.
- [ ] Add one demo-ready task flow covering task creation and interruption.
- [ ] Document local setup, seeded profile behavior, and debug switches.
- [ ] Update the PRD with implementation notes only if the prototype proves or disproves a major assumption.
- [ ] Commit the MVP hardening pass as the handoff-ready checkpoint.

## 13. Spike Tasks Before or During Implementation

These are short research spikes, not large engineering streams:

1. Compare `Electron` vs `Tauri` one last time only if package size becomes a top-level concern.
2. Validate whether gate decisions can be partly heuristic to reduce model cost.
3. Prototype memory retrieval with and without relation edges on the same chat transcript.
4. Decide whether task progress lives only in the main chat or also gets a detail drawer.

## 14. What to Watch Closely During MVP

1. If the gate model is too slow, the whole “自然等待用户说完” story collapses.
2. If graph memory retrieval is too noisy, the product will feel random instead of alive.
3. If task control from main chat is too weak, the assistant side feels fake.
4. If task UI becomes too heavy, the companion side feels like a dashboard product.

## 15. Shipping Standard for MVP

Do not call the MVP successful unless all of the following are true:

1. A user can chat through one desktop UI without obvious fixed-timeout awkwardness.
2. AuraBot can remember at least one stable preference and one future event, then recall both later.
3. AuraBot can create at least one background task from chat and let the user interrupt it.
4. Logs make it possible to explain why AuraBot replied, waited, wrote memory, or spawned a task.

## 16. Recommended Immediate Next Step

If implementation starts now, begin with `Task 1` through `Task 3` only. That gives a visible shell, a persistent chat surface, and a stable technical skeleton before memory and orchestration complexity arrive.
