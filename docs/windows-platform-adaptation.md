# AuraBot Windows Platform Adaptation

> 状态：设计与代码路径已具备第一版，但尚未完成 Windows 实机打包和回归验证。
> 本文只定义适配范围；当前运行时迁移见 [后端迁移路线](backend-runtime-migration.md)。

## Goals

- Add Windows 10/11 x64 support for a normal installable AuraBot desktop app.
- Keep the existing macOS development, runtime, configuration, and packaging behavior intact.
- Make platform-specific behavior explicit and covered by tests where it affects application logic.

## Non-goals

- No code signing, auto-update, auto-start, Microsoft Store distribution, or installer branding polish in this phase.
- No storage engine replacement; AuraBot continues to use `better-sqlite3`.
- No removal of macOS-specific system configuration support or the existing `package:mac` script.

## macOS Regression Contract

- `scripts/package-macos.mjs` and `package:mac` remain available.
- macOS continues to read system-level config from:
  - `/Library/Application Support/AuraBot/aurabot.config.json`
  - `/Library/Application Support/AuraBot/config.json`
- macOS task CLI discovery continues to prefer:
  - `/Applications/Codex.app/Contents/Resources/codex`
  - `/opt/homebrew/bin/claude`
  - `/opt/homebrew/bin/hermes`
- Pet window transparency, always-on-top behavior, click-through settings, and visible-on-all-workspaces behavior must not be weakened on macOS.

## Windows Adaptation Scope

- Add a Windows packaging path with `electron-builder` and a `package:win` script.
- Use Electron `userData` as the Windows configuration location. In normal installs this is `%APPDATA%\AuraBot\config.json`.
- Extend task CLI command discovery to work with Windows `PATH` semantics and `.cmd` / `.exe` commands.
- Guard optional window APIs so a Windows-specific failure does not prevent the pet window from opening.
- Keep the root README platform instructions accurate; the repository does not maintain a duplicate Chinese README.

## Execution Rules

- Any implementation change that contradicts this document must update this document first.
- Prefer additive platform branches and helpers over replacing proven macOS behavior.
- Explicit user configuration always wins over auto-discovered defaults.
- Windows CLI runner support is optional; missing Codex, Claude Code, or Hermes commands must not affect normal chat, memory, reminders, or deterministic task execution.

## Acceptance Checklist

- `cd frontend && npm run typecheck`
- `cd frontend && npm run test:unit`
- `cd frontend && npm run build`
- `cd frontend && npm run package:mac` on macOS
- `cd frontend && npm run package:win` on Windows
- Manual smoke checks:
  - `cd frontend && npm run dev:clean` opens the desktop pet.
  - Live2D reaches ready state.
  - Console and Settings open.
  - Click-through, dragging, and saved pet placement still work.
  - Config file creation opens the userData config path.
  - System notifications can be delivered when supported by the OS.
