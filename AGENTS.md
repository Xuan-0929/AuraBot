# AGENTS.md

## Cursor Cloud specific instructions

### Overview

AuraBot is a desktop-first AI companion Electron app with a Live2D pet surface and chat console. Single-package TypeScript project using electron-vite, React, Zustand, SQLite (embedded via better-sqlite3), and Vitest/Playwright for testing.

### Running services

- **Dev mode**: `corepack pnpm dev:clean` (uses temp user data, deterministic model fallback). On headless Linux, prefix with `xvfb-run --auto-servernum --server-args="-screen 0 1280x1024x24"`.
- **Environment variable** `AURABOT_DETERMINISTIC_MODEL_FALLBACK=1` enables deterministic model responses (no external API required).
- The Electron app requires a display server. Unit/integration tests do **not** require a display.
- WebGL is unavailable in the Cloud Agent container (no GPU); Live2D rendering falls back to a CSS placeholder character. This does not affect test execution.

### Key commands

| Task | Command |
|------|---------|
| Install deps | `corepack enable && corepack pnpm install` |
| Type check | `corepack pnpm typecheck` |
| Unit + integration tests | `corepack pnpm test` |
| Unit tests only | `corepack pnpm test:unit` |
| Integration tests only | `corepack pnpm test:integration` |
| E2E tests | `xvfb-run --auto-servernum --server-args="-screen 0 1280x1024x24" corepack pnpm test:e2e` |
| Build | `corepack pnpm build` |
| Dev (clean) | `xvfb-run --auto-servernum --server-args="-screen 0 1280x1024x24" AURABOT_DETERMINISTIC_MODEL_FALLBACK=1 corepack pnpm dev:clean` |

### Gotchas

- `better-sqlite3` must be rebuilt for Electron ABI before running the app (`electron-rebuild -f -w better-sqlite3`). The `dev` script does this automatically. For tests, `pnpm rebuild:node` rebuilds it for Node.js (also done automatically by the `test` script).
- The `test` command rebuilds better-sqlite3 for Node before running vitest. Running `vitest run` directly without the rebuild may produce native module errors.
- 3 tests in `tests/unit/renderer/store.spec.ts` fail due to a missing mock for `window.aurabot.onLive2DModelStateChanged`. This is a pre-existing issue in the codebase.
- E2E tests require a production build (`electron-vite build`) before Playwright can launch the app. The `test:e2e` script handles this automatically.
- No external database server is needed. SQLite database files are created automatically at runtime.
- No API keys are required for testing — deterministic model fallback is used. For full AI features, configure `config.json` in the Electron userData directory (see README).
