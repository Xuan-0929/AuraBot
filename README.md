# AuraBot

AuraBot is a desktop-first companion AI MVP. The product shape is not a traditional always-open chat app: the default surface is a small Live2D desktop pet, and clicking the character opens a console window for the full main-thread conversation.

The current build validates the first useful loop:

1. A transparent always-on-top Live2D pet lives on the desktop.
2. Clicking the pet opens the AuraBot console.
3. The console handles chat, memory, and task control through the main thread.
4. Task status is visible, but task control stays in chat rather than button panels.

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
| Two-window UX | Small pet window opens a normal console window on click. |
| Local storage | SQLite stores profiles, souls, sessions, messages, memory, tasks, and event logs. |
| Main chat thread | Console messages persist through typed IPC into the main process. |
| Gate logic | Fragmented messages can wait; complete chat, task requests, and task controls are classified. |
| Memory v1 | Preference, event, and follow-up memories can be written and recalled through relation edges. |
| Task v1 | Tasks can be created, queried, interrupted, revised, started, and completed from main chat. |
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

The app should open a small transparent Live2D pet window. Click the character to open the console.

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
| `corepack pnpm test` | 9 test files, 22 tests passed |
| `corepack pnpm typecheck` | passed |
| `corepack pnpm build` | passed |
| `corepack pnpm test:e2e` | 6 tests passed |
| Live2D canvas check | `renderState=ready`, WebGL non-transparent pixels `20780` |

## Project Layout

```text
src/
  main/
    createWindow.ts          # pet window and console window lifecycle
    index.ts                 # Electron app bootstrap
    registerIpc.ts           # typed IPC handlers
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
        tasks/               # task status strip
        status/              # agent status indicator
  core/
    bootstrap/               # service wiring
    orchestration/           # gate and chat pipeline
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

## Live2D Asset

The current pet model is Hiyori from Live2D's official Cubism Web Samples.

Source metadata is kept at:

```text
src/renderer/public/assets/live2d/hiyori/SOURCE.md
```

The Cubism Core runtime is bundled through `live2dcubismcore@1.0.2`; app startup does not fetch the runtime from a CDN.

## Current Boundaries

This is still an MVP skeleton, not a production companion:

1. Chat, gate, and response behavior are deterministic local logic.
2. Memory extraction and relation building are rule-based, not embedding or LLM powered.
3. The task runner validates lifecycle behavior but does not yet call real external tools.
4. Live2D is mounted and clickable, but expressions and motions are not yet driven by agent status.
5. Settings, profile switching, scheduled jobs, desktop notifications, packaging, and auto-start are not implemented yet.

## Next Recommended Work

The current priority order is:

1. Live2D desktop-pet behavior layer: map agent status to motion/expression, separate drag from click, persist pet position, focus existing console on click.
2. Real model adapter layer: add provider config, injectable `gateModel` and `chatModel`, deterministic fallback, streaming replies, and interruption.
3. Memory v2: replace concept extraction, merge duplicate memories, add time-driven reminders, and build a memory debug view.
4. Task executor v2: add real task types, artifact handling, tool execution, and cancellation signals while keeping control in main chat.

See [AuraBot 当前开发状态](docs/aurabot-development-status.md) for the live status board.
