# Model Health and Failure Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete model-health and failure-feedback loop so AuraBot users can see whether `gateModel`, `chatModel`, and `memoryModel` are configured, connected, failing, or falling back to explicit test doubles, instead of guessing from silent behavior.

**Architecture:** Keep the current split between shared contracts, main-process config/state, renderer settings UI, and core model adapters. Add a small `modelHealth` service in the core layer, expose it through IPC, cache the latest results in the main process, and surface the results in the Settings window per model role. Do not change the main product flow or model provider abstraction in this task; only make provider state observable and testable.

**Tech Stack:** Electron, React, TypeScript, Zod, Vitest, existing IPC/event-log infrastructure

---

## 1. Scope

This plan only covers the productization of model connectivity and model failure visibility.

It does not cover:

1. User-message interruption for in-flight generation
2. System notifications for proactive reminders
3. Embedding/vector retrieval
4. Multi-profile UI
5. Task runner redesign

## 2. Implementation Target

After this work lands, AuraBot should provide the following product behavior:

1. The Settings window shows health/state for `chatModel`, `gateModel`, and `memoryModel`.
2. Each role can be tested independently with a `Test Connection` action.
3. Users can tell the difference between:
   - not configured
   - configured but not yet tested
   - testing
   - connected
   - failed
   - using explicit test double
4. Common configuration and invocation failures are turned into short product-facing explanations.
5. The app emits event logs for health checks and test failures.

## 3. Key Decisions

### Decision A: Keep health state out of persistent product config

The JSON config file should remain user-authored configuration, not runtime health storage.

Health state should live in main-process memory and be regenerated on app launch.

### Decision B: Separate static config validity from live connectivity

There are two different questions:

1. Is the role configured enough to attempt a call?
2. Can the role actually reach the configured provider right now?

The first should be available immediately after config load. The second should come from explicit test or first real invocation.

### Decision C: Do not add background auto-probing in v1

For the first version, manual `Test Connection` plus passive error capture from real use is enough.

This avoids:

1. unnecessary startup latency
2. surprise API cost
3. harder-to-explain “why did AuraBot ping the model by itself?” behavior

### Decision D: Keep explicit deterministic doubles test-only

User-facing runs should still require real model config. The only “fallback-like” state exposed to users should be an explicit test-double state, and only when the app is running in a test/developer mode that intentionally enables it.

## 4. State Model

Introduce a shared health model for each role:

```ts
export type ModelRoleKey = "chatModel" | "gateModel" | "memoryModel";

export type ModelHealthState =
  | "not_configured"
  | "configured"
  | "testing"
  | "connected"
  | "error"
  | "using_test_double";

export interface PublicModelHealth {
  role: ModelRoleKey;
  provider: string;
  enabled: boolean;
  configured: boolean;
  hasApiKey: boolean;
  model?: string;
  baseUrl?: string;
  state: ModelHealthState;
  lastCheckedAt?: string;
  lastErrorCode?: string;
  lastErrorMessage?: string;
}
```

Renderer-facing `PublicAppConfig` should continue to contain stable config summary, but it should also expose health status separately so that config and runtime state are not conflated.

## 5. File Map

### Shared contracts

- Modify: [src/shared/contracts/settings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/settings.ts)
- Modify: [src/shared/contracts/ipc.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/ipc.ts)
- Modify: [src/shared/schemas/config.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/config.ts)

### Core model health

- Add: [src/core/llm/modelHealth.ts](/Users/xuan/Documents/AuraBot/src/core/llm/modelHealth.ts)
- Modify: [src/core/llm/modelErrors.ts](/Users/xuan/Documents/AuraBot/src/core/llm/modelErrors.ts)
- Reuse: [src/core/llm/chatModel.ts](/Users/xuan/Documents/AuraBot/src/core/llm/chatModel.ts)
- Reuse: [src/core/llm/gateModel.ts](/Users/xuan/Documents/AuraBot/src/core/llm/gateModel.ts)
- Reuse: [src/core/memory/modelBackedMemoryModel.ts](/Users/xuan/Documents/AuraBot/src/core/memory/modelBackedMemoryModel.ts)

### Main process state and IPC

- Modify: [src/main/appConfigStore.ts](/Users/xuan/Documents/AuraBot/src/main/appConfigStore.ts)
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/main/index.ts](/Users/xuan/Documents/AuraBot/src/main/index.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)

### Renderer

- Modify: [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Modify: [src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)
- Modify: [src/renderer/src/i18n.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/i18n.ts)
- Modify: [src/renderer/src/styles.css](/Users/xuan/Documents/AuraBot/src/renderer/src/styles.css)

### Tests

- Modify: [tests/unit/shared/settings.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/shared/settings.spec.ts)
- Add: [tests/unit/core/llm/modelHealth.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/llm/modelHealth.spec.ts)
- Modify: [tests/unit/main/appConfigStore.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/main/appConfigStore.spec.ts)
- Modify: [tests/unit/renderer/settingsWindow.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsWindow.spec.tsx)
- Modify: [tests/e2e/app-launch.spec.ts](/Users/xuan/Documents/AuraBot/tests/e2e/app-launch.spec.ts)

### Docs

- Modify: [docs/aurabot-development-status.md](/Users/xuan/Documents/AuraBot/docs/aurabot-development-status.md)
- Optionally modify: [README.md](/Users/xuan/Documents/AuraBot/README.md)

## 6. Execution Tasks

### Task 1: Define Shared Model Health Contracts

**Files:**
- Modify: [src/shared/contracts/settings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/settings.ts)
- Modify: [src/shared/contracts/ipc.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/ipc.ts)
- Test: [tests/unit/shared/settings.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/shared/settings.spec.ts)

- [ ] Add `ModelHealthState`, `PublicModelHealth`, and a renderer-safe container shape such as `PublicModelHealthMap`.
- [ ] Extend `PublicAppConfig` only if a small embedded summary is useful; otherwise expose health through separate IPC to avoid mixing config and runtime state.
- [ ] Add IPC channel names for:
  - `config:get-model-health`
  - `config:test-model-connection`
- [ ] Add typed request/response contracts for testing one role at a time.
- [ ] Update shared tests so public settings contracts reject “local fallback” as a user-facing provider and validate the new health shape.

### Task 2: Normalize Model Error Taxonomy

**Files:**
- Modify: [src/core/llm/modelErrors.ts](/Users/xuan/Documents/AuraBot/src/core/llm/modelErrors.ts)
- Test: [tests/unit/core/llm/modelHealth.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/llm/modelHealth.spec.ts)

- [ ] Expand the current error taxonomy beyond `ModelConfigurationError` and `ModelInvocationError`.
- [ ] Add stable error classification helpers for at least:
  - missing configuration
  - unsupported provider
  - authentication failure
  - timeout
  - network failure
  - malformed provider response
  - unknown invocation failure
- [ ] Add a renderer-safe “short message” formatter so the UI can show “API Key 无效” rather than raw provider stack traces.
- [ ] Keep the original raw message available for logs and deeper debugging, but separate it from product-facing copy.

### Task 3: Add a Core Model Health Service

**Files:**
- Add: [src/core/llm/modelHealth.ts](/Users/xuan/Documents/AuraBot/src/core/llm/modelHealth.ts)
- Reuse: [src/core/llm/chatModel.ts](/Users/xuan/Documents/AuraBot/src/core/llm/chatModel.ts)
- Reuse: [src/core/llm/gateModel.ts](/Users/xuan/Documents/AuraBot/src/core/llm/gateModel.ts)
- Reuse: [src/core/memory/modelBackedMemoryModel.ts](/Users/xuan/Documents/AuraBot/src/core/memory/modelBackedMemoryModel.ts)
- Test: [tests/unit/core/llm/modelHealth.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/llm/modelHealth.spec.ts)

- [ ] Create a `modelHealth` service that can answer two questions per role:
  - config summary status
  - live connectivity test result
- [ ] Implement a fast `inspectModelConfig(roleConfig)` path that returns `not_configured` or `configured` without network calls.
- [ ] Implement a `testModelConnection(role, config)` path that performs a minimal provider probe.
- [ ] For `openai-compatible`, use the smallest possible safe request.
- [ ] For `langchain-openai`, probe through the LangChain path, not by bypassing it.
- [ ] Return a normalized `PublicModelHealth` result regardless of provider or failure mode.
- [ ] Ensure `memoryModel` tests do not accidentally trigger heavy write/merge/relation workflows; the health probe should stay lightweight.

### Task 4: Cache and Serve Health State in the Main Process

**Files:**
- Modify: [src/main/appConfigStore.ts](/Users/xuan/Documents/AuraBot/src/main/appConfigStore.ts)
- Modify: [src/main/index.ts](/Users/xuan/Documents/AuraBot/src/main/index.ts)
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
- Test: [tests/unit/main/appConfigStore.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/main/appConfigStore.spec.ts)

- [ ] Add an in-memory health snapshot store keyed by `chatModel`, `gateModel`, and `memoryModel`.
- [ ] Seed initial health state from static config inspection during bootstrap.
- [ ] Expose an IPC handler to fetch current health without re-testing.
- [ ] Expose an IPC handler to test a single role on demand.
- [ ] When provider settings are changed from the Settings window, invalidate the cached health for that role back to `configured` or `not_configured`.
- [ ] Record `lastCheckedAt`, `lastErrorCode`, and `lastErrorMessage` in the snapshot, but do not persist them into user config.

### Task 5: Surface Health in the Settings Window

**Files:**
- Modify: [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Modify: [src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)
- Modify: [src/renderer/src/i18n.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/i18n.ts)
- Modify: [src/renderer/src/styles.css](/Users/xuan/Documents/AuraBot/src/renderer/src/styles.css)
- Test: [tests/unit/renderer/settingsWindow.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsWindow.spec.tsx)

- [ ] Add a visible health/status row to each model card.
- [ ] Show human-readable states:
  - 未配置
  - 已配置
  - 测试中
  - 已连接
  - 连接失败
  - 测试替身
- [ ] Add a `Test Connection` button per role.
- [ ] Disable or debounce the button while that role is already testing.
- [ ] Show the latest short error message when state is `error`.
- [ ] Optionally show `lastCheckedAt` in a compact secondary text row if it does not clutter the card.
- [ ] Keep current provider selector behavior intact.

### Task 6: Add Event Logging for Health Checks

**Files:**
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Reuse: [src/core/telemetry/eventLogService.ts](/Users/xuan/Documents/AuraBot/src/core/telemetry/eventLogService.ts)
- Test: [tests/unit/core/llm/modelHealth.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/llm/modelHealth.spec.ts)

- [ ] Emit structured events for:
  - `model.health.checked`
  - `model.health.failed`
  - `model.health.test_requested`
- [ ] Include role, provider, state, and normalized error code in the payload.
- [ ] Avoid logging secrets such as API keys or raw authorization headers.

### Task 7: Document the Product Behavior

**Files:**
- Modify: [docs/aurabot-development-status.md](/Users/xuan/Documents/AuraBot/docs/aurabot-development-status.md)
- Optionally modify: [README.md](/Users/xuan/Documents/AuraBot/README.md)

- [ ] Update the development status doc to say model provider selection is now paired with health diagnostics.
- [ ] Update the current boundary section so the repo no longer implies users must infer provider failures from behavior.
- [ ] If needed, add one short README note explaining that provider selection alone is not enough; users can validate connectivity from Settings.

## 7. Test Strategy

### Unit tests

1. `modelHealth` returns `not_configured` when key fields are missing.
2. `modelHealth` returns `connected` for a mocked successful provider probe.
3. `modelHealth` maps representative failures to stable error codes/messages.
4. Settings window renders status text and disables the test button while loading.

### Integration-ish main-process tests

1. Provider patch invalidates only the changed role's health state.
2. A successful test updates cached `lastCheckedAt`.
3. A failed test preserves renderer-safe health state and logs the failure.

### E2E smoke

1. Open Settings.
2. See model cards.
3. Trigger one health test.
4. Observe either a success badge or a visible failure message.

## 8. Recommended Order of Implementation

Do the work in this order:

1. Shared contracts
2. Error taxonomy
3. Core `modelHealth` service
4. Main-process cache + IPC
5. Renderer settings UI
6. Event logs
7. Docs and e2e polish

That order keeps the renderer from inventing state names before the core and IPC contracts are stable.

## 9. Non-Goals for This Change

Do not mix these into the same implementation batch:

1. User-message interruption during streaming replies
2. System notification permissions
3. Background auto-ping of providers
4. Full “open config and immediately re-test all models” workflow
5. Embedding support

## 10. What This Unlocks Next

Once this lands, the next two follow-up tracks become much safer:

1. Making model failure paths visible inside the main chat flow
2. Pushing proactive reminders and memoryModel behavior harder, because the team can finally tell whether failures are product logic failures or provider connectivity failures
