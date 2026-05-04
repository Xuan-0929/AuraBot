# AuraBot MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first usable AuraBot desktop MVP that validates four core hypotheses: a Live2D-first desktop companion shell feels less like a tool UI, natural chat timing beats fixed timeout, mixed memory beats flat memory, and a chat-first companion can still control background tasks without losing “alive” feeling.

**Architecture:** Use an Electron desktop shell with a React renderer, a Node/TypeScript orchestration core, and a local SQLite store. Keep one user-facing main chat session per active profile, route background work into task sessions with capped depth, and separate lightweight gate/classification logic from main response generation to reduce cost and context pollution.

**Tech Stack:** Electron, React, TypeScript, Vite, Zustand, better-sqlite3, Zod, p-queue, Vitest, Playwright

---

## 1. This Plan Covers

This document is the execution-facing breakdown of the PRD at [AuraBot 产品需求文档](/Users/xuan/.codex/worktrees/8fcd/New%20project%204/docs/PRD-v1.md).

It does not try to build the entire AuraBot vision in one pass. It narrows the first implementation to a testable MVP plus the minimum scaffolding needed not to paint the codebase into a corner.

## 2. MVP Boundary

### 2.1 Must Ship in MVP

1. Live2D-first desktop companion shell with one active AuraBot profile: the default surface is a small desktop pet, and hover controls open the full console when needed.
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
   - view task status as a lightweight console status strip
   - interrupt or revise tasks through the main chat thread
8. Local event logging and basic observability for debugging response timing and memory writes.

### 2.2 Can Land Right After MVP

1. Multi-profile support beyond a single active profile.
2. Scheduled jobs and reminders.
3. Desktop notifications for proactive follow-ups.
4. External channel adapters such as QQ and WeChat.

### 2.3 Explicitly Out of Scope for MVP

1. Game integration.
2. Production Live2D asset polish beyond mounting a verified official sample model.
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
| Live2D 桌宠壳层 | Make a small always-on-top desktop pet the first visual surface; expose hover controls for drag, voice, and console |
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
          live2d/
            Live2DModelCanvas.tsx
          pet/
            DesktopPet.tsx
          console/
            ConsoleWindow.tsx
          chat/
            ChatWindow.tsx
            MessageList.tsx
            Composer.tsx
            useChatController.ts
          tasks/
            TaskStatusOverlay.tsx
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

**Outcome:** The app boots locally into a Live2D-first desktop pet, persists state, and has typed boundaries between UI and orchestration.

**Success criteria:**

1. Desktop app opens a small transparent pet window with a visible Live2D model surface.
2. Hovering the character reveals a console control that opens a normal console window for the main conversation.
3. Renderer can send a typed message to the main process.
4. SQLite database initializes with migrations.
5. Test tooling runs.

### Milestone B: Chat Core

**Outcome:** The user can chat with a single AuraBot profile through the console opened from the desktop pet and see persisted messages plus agent status.

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
10. Task status strip in the console
11. Task interrupt/revise through main chat
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
3. A task summary plus interrupt/revise chat commands are enough for the first build.

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

### 10.1 Desktop Pet Surface

Must show:

1. A verified Live2D model surface in a small transparent always-on-top window.
2. Hover controls below the character for drag, voice input, and console opening.
3. Temporary message bubbles for task completion and pet-voice replies.
4. No chat input, task buttons, or full dashboard controls on the pet surface.

### 10.2 Console Conversation Window

Must show:

1. Main message stream.
2. Input box and send action.
3. Agent status.
4. Lightweight memory-aware or task-aware system hints when useful.

### 10.3 Task Status Strip

Must show:

1. Task title
2. Status: queued, running, waiting, completed, failed, interrupted
3. Short latest progress text
4. No direct task-control buttons; query, interrupt, start, and revise happen through the main chat thread.

### 10.4 Settings Surface

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

## 12. Current Implementation Status

当前执行状态详见：[AuraBot 当前开发状态](/Users/xuan/.codex/worktrees/8fcd/New%20project%204/docs/aurabot-development-status.md)。

| 原计划任务 | 当前状态 | 说明 |
| --- | --- | --- |
| Task 1: Bootstrap the Desktop Workspace | 已完成 | `pnpm`、Electron/Vite/React/TypeScript、typed IPC、Vitest、Playwright 均已落地。 |
| Task 2: Add Local Storage and Repositories | 已完成 | SQLite migrations 和 profile/session/message/memory/task repositories 已落地并有集成测试。 |
| Task 3: Build the Main Chat UI Shell | 已完成并调整形态 | 不再是传统主聊天窗口；当前为桌面小人 + 悬浮控制打开控制台视窗。 |
| Task 4: Add Profile and Soul Loading | 部分完成 | 默认 profile/soul 已可 seed；还缺专门的 soul schema 单元测试和 Profile UI。 |
| Task 5: Implement the Gate Evaluator | 已完成 MVP | 支持碎片等待、完整回复、话题结束、chat/task/task_control 分类和事件日志；可由轻量外部模型接管。 |
| Task 6: Implement the Main Chat Orchestrator | 已完成 MVP | 支持消息持久化、控制台用户消息乐观上屏、注入式 gate/chat model、memory 写入/召回、按聊天 hint 控制特定 task；还缺真实流式生成取消。 |
| Task 7: Add Mixed Memory v1 | 已完成 MVP | 支持 preference/event/follow_up，摘要层、关系边和 bounded graph traversal。 |
| Task 8: Add the Task Engine and Console Task Status Strip | 已完成 MVP | 支持主聊天创建、按目标 hint 查询/打断/修改/启动任务；任务条只展示状态，不放控制按钮；显式配置后可调用本机 Codex 子任务 runner。 |
| Task 9: Add Debugging and Event Log Surfaces | 部分完成 | `event_logs` 已记录关键事件；还缺 settings/debug 视窗和日志开关。 |
| Task 10: Hardening Pass and MVP Demo Flow | 部分完成 | e2e、截图、`README.md` 和本地使用说明已覆盖；还缺打包/发布说明。 |

## 13. Verified Evidence

最近一次完整验证记录：

1. `corepack pnpm test`：23 个 test files、74 个 tests 通过。
2. `corepack pnpm typecheck`：通过。
3. `corepack pnpm build`：通过。
4. `corepack pnpm test:e2e`：11 个 e2e tests 通过。
5. Live2D 冷启动检查：`dev:clean` 可见渲染 Hiyori，`renderState=ready`，`motionGroup=Idle`。

当前可视化产物：

1. `artifacts/desktop-pet.png`
2. `artifacts/console-window.png`

## 14. Recommended Immediate Next Work

### Next 1: Live2D 桌宠行为层

先让小人真正像桌面伴侣，而不是静态模型挂载。

建议交付：

1. 继续打磨 agent status 到 Live2D motion/expression 的映射质量。
2. 继续细化点击穿透 hit testing，让主体穿透和控制区点击可以同时稳定成立。
3. 接入稳定 STT adapter，替换当前浏览器原生语音识别 MVP。
4. 将主动提醒/主动对话触发接入当前桌宠气泡通道。
5. 增加开发期诊断面板，展示 Live2D renderState、motion、窗口 bounds、click-through 状态。

### Next 2: 真实模型适配层

把可配置模型调用打磨成可长期运行的模型层，同时保留测试稳定性。

建议交付：

1. 已完成：provider config 支持 `API Base URL`、`API Key`、`chatModel`、`gateModel`、enable switch。
2. 已完成：定义 `gateModel` / `chatModel` adapter interface，并接入 OpenAI-compatible provider。
3. 已完成：保留 deterministic adapter 作为无 key fallback 和测试 adapter。
4. 已完成：控制台用户消息和输入框清空本地即时提交，不再等待 gate/chat 模型返回。
5. 下一步：加入流式回复和用户新消息中断生成。
6. 下一步：增加 provider 重试、超时、失败事件日志和连接状态展示。

### Next 3: Memory v2

让 memory 更像联想，而不只是规则概念命中。

建议交付：

1. 用 LLM 或 embedding 替换轻量概念抽取层。
2. 增加重复记忆合并和旧记忆更新策略。
3. 时间驱动记忆接入到期前后提醒。
4. 做一个记忆调试视图，显示写入、召回和边跳转。
5. 构造固定对话集，比较关键词召回和图状联想召回。

### Next 4: Task Executor v2

从 deterministic runner 进入真实可执行代理。

建议交付：

1. 已完成：opt-in Codex 子任务 runner，可在创建任务后后台调用本机 `codex exec`，实时广播 JSONL 进度，查询时暴露最近进展，并把最终结果回流主聊天。
2. 下一步：设计真实 `TaskRegistry` 类型和输入输出协议。
3. 下一步：明确 task artifact 写入和主线程回流格式。
4. 已完成：主聊天打断任务会取消底层 runner / Codex 子进程，避免只改 UI 状态。
5. 已完成：服务启动时会把上次遗留的 running/waiting 任务标记为失联失败，避免永久卡在运行态。
6. 下一步：补真正 resume 和更完整的 artifact 回流协议。
7. 继续保持控制面在主聊天线程，不新增任务按钮面板。

### Next 5: Handoff and Push Prep

建议在 push 前补齐：

1. `README.md` 已补。
2. `artifacts/*.png` 已确认可以提交。
3. push 前再跑一轮 fresh `test / typecheck / build / test:e2e`。

## 15. Push Decision

当前不自动 push。建议先 review 本文档更新：

1. 如果当前状态和下一步优先级符合预期，再提交并 push。
2. 如果下一步想先做模型 API、memory v2 或 Live2D 行为层中的某一项，先调整文档再 push。
3. 截图已确认可以进入仓库，push 前应一并 stage `artifacts/desktop-pet.png` 和 `artifacts/console-window.png`。
