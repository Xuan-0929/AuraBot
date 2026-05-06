# AuraBot

[中文 README](README.zh-CN.md)

AuraBot is a desktop-first companion AI MVP. The product shape is not a traditional always-open chat app: the default surface is a small Live2D desktop pet, and hovering the character reveals lightweight controls for drag, voice input, inline quick chat, and the full console conversation.

The current build validates the first useful loop:

1. A transparent always-on-top Live2D pet lives on the desktop.
2. Hovering the pet reveals semi-transparent drag, voice, console, and quick-chat controls.
3. The console handles chat, memory, and task control through the main thread.
4. Task status is visible through cards, with main-chat control plus quick cancel/delete buttons for common card actions.
5. Task completion, pet-voice replies, pet quick-chat input, and due follow-ups can surface through desktop-pet bubbles.

## Current Demo

Desktop pet:

![AuraBot desktop pet](artifacts/desktop-pet.png)

Console window:

![AuraBot console window](artifacts/console-window.png)

## What Works Now

| Area | Status |
| --- | --- |
| Desktop shell | Electron + React + TypeScript + Vite app boots locally. |
| Live2D surface | Official Live2D Hiyori sample model renders in the pet window. |
| Two-window UX | Small pet window opens or focuses a normal console window from the hover control. |
| Pet behavior settings | Console settings can let clicks pass through the pet window. |
| Pet placement | Dragged pet window positions are persisted and clamped back into the visible work area. |
| Pet status behavior | Agent status is broadcast to the pet and mapped onto Hiyori's available motion groups; the pet also tracks the system cursor for eye focus even outside the pet window. |
| Pet quick chat | Hovering the Live2D model reveals a small `Chat` bubble that expands into an inline input and sends messages back to the main chat as `pet_text`. |
| Voice input MVP | Pet hover control can use browser SpeechRecognition when the runtime supports it. |
| Local storage | SQLite stores profiles, souls, sessions, messages, memory, tasks, and event logs. |
| Main chat thread | Console and pet quick-chat messages persist through typed IPC into the main process, with optimistic user messages and streaming assistant updates. |
| Model roles | `gateModel`, `chatModel`, and `memoryModel` are injectable; all default to local deterministic fallback and can use OpenAI-compatible APIs. |
| Gate logic | Fragmented messages can wait; complete chat, task requests, task controls, and topic endings are classified, with model-backed gate decisions when configured. |
| Memory v1/v2 | Preference, event, and follow-up memories can be written and recalled through relation edges; while the app is running, a background scheduler can surface due follow-ups through desktop-pet bubbles. |
| Task v1 | Tasks can be created, queried, interrupted, revised, started, and completed from main chat; task cards expose quick cancel/delete controls; opt-in Codex, Claude Code, or Hermes CLI runners can execute real delegated tasks by priority. |
| Verification | Unit, integration, build, and e2e checks are wired. |

## Quick Start

Requirements:

1. Node.js with Corepack available.
2. macOS is the current development target.

Install dependencies:

```bash
corepack enable
corepack pnpm install
```

Run the desktop app in development mode:

```bash
corepack pnpm dev
```

The app should open a small transparent Live2D pet window. The bundled Hiyori model is loaded from:

```text
src/renderer/public/assets/live2d/hiyori/Hiyori.model3.json
```

Hover the character and click `Console` to open the console window. In the console composer:

1. `Enter` sends the message.
2. `Ctrl+Enter` inserts a line break.
3. The user message is added to the conversation immediately; gate/model work continues in the background and assistant/task output arrives later.

Run with clean temporary user data and a small Live2D debug badge:

```bash
corepack pnpm dev:clean
```

Clean launches create a temporary user data directory, set `AURABOT_DEBUG_PET=1`, and skip the macOS system-level AuraBot config file. Use this mode when checking whether the desktop pet and Live2D model can render without your local API config influencing the run.

Run the production build with clean temporary user data:

```bash
corepack pnpm start:clean
```

## External Model API

AuraBot now has three separate model roles:

| Role | Intended model |
| --- | --- |
| `gateModel` | Lightweight, fast, low-cost model for reply timing, topic-ended judgment, chat/task classification, and task-control detection. |
| `chatModel` | Stronger reasoning model for final user-facing conversation. |
| `memoryModel` | Structured-output model for memory extraction, duplicate-memory merging, and candidate relation-edge planning. |

Without config, all three roles use local deterministic fallback. To enable an OpenAI-compatible provider, create either file in the Electron user data directory:

```text
aurabot.config.json
config.json
```

On macOS, normal launches also read system-level config from:

```text
/Library/Application Support/AuraBot/aurabot.config.json
/Library/Application Support/AuraBot/config.json
```

For day-to-day local testing on macOS, the simplest normal path is:

```text
/Library/Application Support/AuraBot/config.json
```

Create or edit it with your preferred editor, for example:

```bash
sudo mkdir -p "/Library/Application Support/AuraBot"
sudo nano "/Library/Application Support/AuraBot/config.json"
```

Example:

```json
{
  "gateModel": {
    "enabled": true,
    "provider": "openai-compatible",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-lightweight-model",
    "temperature": 0,
    "timeoutMs": 8000
  },
  "chatModel": {
    "enabled": true,
    "provider": "openai-compatible",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-strong-reasoning-model",
    "temperature": 0.7,
    "timeoutMs": 30000
  },
  "memoryModel": {
    "enabled": true,
    "provider": "openai-compatible",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-structured-memory-model",
    "temperature": 0,
    "timeoutMs": 12000
  },
  "taskRunner": {
    "provider": "cli",
    "runners": [
      {
        "id": "codex",
        "enabled": true,
        "priority": 1,
        "command": "/Applications/Codex.app/Contents/Resources/codex",
        "workingDirectory": "/path/to/AuraBot",
        "sandbox": "workspace-write",
        "approvalPolicy": "never",
        "timeoutMs": 300000
      },
      {
        "id": "claude",
        "enabled": false,
        "priority": 2,
        "command": "/opt/homebrew/bin/claude",
        "workingDirectory": "/path/to/AuraBot",
        "approvalPolicy": "never",
        "timeoutMs": 300000
      },
      {
        "id": "hermes",
        "enabled": false,
        "priority": 3,
        "command": "hermes",
        "workingDirectory": "/path/to/AuraBot",
        "timeoutMs": 300000
      }
    ],
    "sandbox": "workspace-write",
    "approvalPolicy": "never",
    "timeoutMs": 300000
  }
}
```

The `gateModel`, `chatModel`, and `memoryModel` roles also have small LangChain-backed proof-of-concept paths. Set any role's `provider` to `"langchain-openai"` to route that role through `@langchain/openai` while keeping the other roles on the existing provider path. The Settings window can switch each role between local fallback, OpenAI-compatible, and LangChain OpenAI; API keys, base URLs, and model names still live in `config.json`. For `gateModel` and `memoryModel`, AuraBot uses LangChain structured output and then keeps the existing zod validation, routing/memory logic, and deterministic fallback behavior.

For structured gate decisions through LangChain:

```json
{
  "gateModel": {
    "enabled": true,
    "provider": "langchain-openai",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-lightweight-gate-model",
    "temperature": 0,
    "timeoutMs": 8000
  }
}
```

```json
{
  "chatModel": {
    "enabled": true,
    "provider": "langchain-openai",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-strong-reasoning-model",
    "temperature": 0.7,
    "timeoutMs": 30000
  }
}
```

For structured memory extraction and recall planning through LangChain:

```json
{
  "memoryModel": {
    "enabled": true,
    "provider": "langchain-openai",
    "baseUrl": "https://api.example.com/v1",
    "apiKey": "your-api-key",
    "model": "your-structured-memory-model",
    "temperature": 0,
    "timeoutMs": 12000
  }
}
```

Use the provider API root as `baseUrl`, usually ending in `/v1`. Do not point `baseUrl` directly at `/chat/completions`; AuraBot appends the chat completions path itself.

For temporary development launches, environment variables can override the file:

```bash
AURABOT_MODEL_PROVIDER=openai-compatible \
AURABOT_MODEL_BASE_URL=https://api.example.com/v1 \
AURABOT_MODEL_API_KEY=your-api-key \
AURABOT_GATE_MODEL_ENABLED=1 \
AURABOT_GATE_MODEL_NAME=your-lightweight-model \
AURABOT_GATE_MODEL_TIMEOUT_MS=8000 \
AURABOT_CHAT_MODEL_ENABLED=1 \
AURABOT_CHAT_MODEL_NAME=your-strong-reasoning-model \
AURABOT_CHAT_MODEL_TIMEOUT_MS=30000 \
AURABOT_MEMORY_MODEL_ENABLED=1 \
AURABOT_MEMORY_MODEL_NAME=your-structured-memory-model \
AURABOT_MEMORY_MODEL_TIMEOUT_MS=12000 \
AURABOT_TASK_RUNNER=codex \
AURABOT_CODEX_COMMAND=/Applications/Codex.app/Contents/Resources/codex \
AURABOT_CODEX_WORKDIR=/path/to/AuraBot \
AURABOT_CODEX_SANDBOX=workspace-write \
AURABOT_CODEX_APPROVAL_POLICY=never \
corepack pnpm dev:clean
```

Keep API keys local and out of git. `corepack pnpm dev` reads the normal system-level config above; `corepack pnpm dev:clean` ignores it unless you pass env vars.

## Task CLI Runners

Task execution defaults to the deterministic runner used by tests and demos. To let AuraBot delegate a queued task to local CLI agents, open Settings and use the Task CLI section, or set `taskRunner.provider` to `cli` with `taskRunner.runners` in config. Lower `priority` values are tried first. If a higher-priority enabled command is unavailable, AuraBot skips it and starts the next enabled runner.

Legacy Codex-only config still works: setting `taskRunner.provider` to `codex` or `AURABOT_TASK_RUNNER=codex` enables the Codex runner and maps the old `codexCommand`, `workingDirectory`, `model`, `sandbox`, `approvalPolicy`, and `timeoutMs` fields into the new runner list.

Current main-chat flow:

1. Say something task-like, for example `帮我做一份 GitHub 周报。`
2. AuraBot creates a child task session and keeps the main chat as the control plane.
3. When any task CLI runner is enabled, AuraBot immediately starts the highest-priority available child runner in the background and the task status strip moves from `queued` to `running`.
4. Codex runs through `codex exec`; Claude Code runs through `claude --print`; Hermes runs as a configurable stdin/stdout CLI command.
5. While it runs, progress updates are stored on the task and broadcast to the console status strip; asking `这个任务做到哪了？` returns the status, latest progress, and latest task event.
6. AuraBot stores the final CLI output in task events and replies in the main console when the child task settles.

When the default deterministic runner is active, newly created tasks remain `queued` until the main chat says `开始做这个任务。`. This keeps local tests and demos predictable.

Default task CLI execution settings are intentionally conservative:

| Field | Default |
| --- | --- |
| `sandbox` | `workspace-write` |
| `approvalPolicy` | `never` |
| `timeoutMs` | `300000` |
| `workingDirectory` | current process working directory unless configured |

The runner records `task.agent.started`, `task.agent.progress`, `task.agent.completed`, and `task.agent.failed` events. Failed child runs are reported as failed in the main chat instead of being treated as completed work.

## Verification Commands

Run all unit and integration tests:

```bash
corepack pnpm test
```

Run type checking:

```bash
corepack pnpm typecheck
```

Build the Electron app:

```bash
corepack pnpm build
```

Run end-to-end tests:

```bash
corepack pnpm test:e2e
```

The last verified local run recorded:

| Check | Result |
| --- | --- |
| `corepack pnpm test` | 23 test files, 74 tests passed |
| `corepack pnpm typecheck` | passed |
| `corepack pnpm build` | passed |
| `corepack pnpm test:e2e` | 11 tests passed |
| Live2D cold-start check | `dev:clean` renders the Hiyori model with `renderState=ready` and `motionGroup=Idle` |

## Project Layout

```text
src/
  main/
    createWindow.ts          # pet window and console window lifecycle
    index.ts                 # Electron app bootstrap
    petSettingsStore.ts      # persisted desktop pet behavior settings
    petWindowStateStore.ts   # persisted pet window placement
    registerIpc.ts           # typed IPC handlers
    windowPlacement.ts       # console placement near the pet window
  preload/
    index.ts                 # safe renderer API bridge
  renderer/
    public/assets/live2d/    # bundled Live2D sample model
    src/
      App.tsx                # pet/console route switch
      app/store.ts           # renderer state and IPC calls
      features/
        pet/                 # desktop pet surface
        live2d/              # Pixi + Live2D renderer
        console/             # full conversation window
        chat/                # composer and message list
        settings/            # compact pet behavior settings
        tasks/               # task status strip
        status/              # agent status indicator
  core/
    bootstrap/               # service wiring
    orchestration/           # gate and chat pipeline
    llm/                     # model-role interfaces and OpenAI-compatible adapters
    memory/                  # memory write, retrieval, associations
    tasks/                   # task lifecycle and deterministic runner
    storage/                 # SQLite migrations and repositories
    profiles/                # default profile and soul seed
    telemetry/               # event log service
  shared/
    contracts/               # IPC and chat DTOs
    schemas/                 # Zod schemas
tests/
  unit/
  integration/
  e2e/
docs/
  PRD-v1.md
  TODO.md
  TS.md
  aurabot-development-status.md
  aurabot-memory-structure.md
```

## Live2D Model Loading

The current pet model is Hiyori from Live2D's official Cubism Web Samples. No extra download is needed for the current checkout.

Runtime model URL:

```text
assets/live2d/hiyori/Hiyori.model3.json
```

Renderer constant:

```text
src/renderer/src/features/pet/DesktopPet.tsx
```

Current code:

```ts
const MODEL_PATH = "assets/live2d/hiyori/Hiyori.model3.json";
```

To replace the model:

1. Put the Cubism 4 model folder under `src/renderer/public/assets/live2d/<model-name>/`.
2. Keep the model's `*.model3.json`, textures, motions, physics, and expression files in the relative layout expected by that manifest.
3. Update `MODEL_PATH` in `src/renderer/src/features/pet/DesktopPet.tsx`.
4. Add or update a `SOURCE.md` in the model folder with source and license notes.
5. Start with `corepack pnpm dev:clean`; the debug badge should show `ready` when the model loads.

Source metadata is kept at:

```text
src/renderer/public/assets/live2d/hiyori/SOURCE.md
```

The Cubism Core runtime is bundled through `live2dcubismcore@1.0.2`; app startup does not fetch the runtime from a CDN.

## Current Boundaries

This is still an MVP skeleton, not a production companion:

1. Model roles default to deterministic local fallback; external calls require local config or environment variables.
2. User messages render immediately and assistant messages can stream through update broadcasts, but user-message interruption of an in-flight generation is not implemented yet.
3. Memory can use `memoryModel` for structured extraction, duplicate-memory merging, candidate relation-edge planning, recall query-term generation, and seed-node reranking; embedding is not implemented yet.
4. The task runner can call local Codex, Claude Code, or Hermes when explicitly configured, broadcasts child progress, cancels the child process when interrupted, and marks orphaned running tasks failed on startup; true resume and richer artifact handling are not implemented yet.
5. Live2D status behavior uses the bundled Hiyori motion groups and global cursor focus; custom expressions and richer animation tuning are not implemented yet.
6. Voice input currently depends on Chromium's `SpeechRecognition`; a stable local or model-backed STT adapter is still needed.
7. Profile switching, scheduled jobs, desktop notifications, packaging, and auto-start are not implemented yet.

## Next Recommended Work

The current priority order is:

1. Live2D desktop-pet behavior layer: tune status motions, add richer expression assets, keep hardening click-through hit testing, and add diagnostics for global cursor tracking.
2. Real model adapter layer v2: add in-flight interruption, retry/backoff, and a visible model connection status now that streaming replies are wired.
3. Memory v2: keep expanding realistic multi-turn evaluation fixtures; the app-running follow-up background scheduler plus desktop-pet bubble path is wired in, Memory Debug shows the latest recall trace, and `corepack pnpm eval:memory` runs the fixed recall fixture.
4. Task executor v2: add real task types, artifact handling, tool execution, and cancellation signals while keeping main chat as the primary control plane.
5. Main-thread task control v2: extend the current hint-based router with richer references like "第一个", "刚才那个", and task aliases.

See [AuraBot 当前开发状态](docs/aurabot-development-status.md) for the live status board.
