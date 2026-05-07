# AuraBot UI Polish + Notifications Stage 3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the remaining A1 UI polish, move Memory Debug out of the console into full settings, and ship an app-running system-notification MVP for follow-up, time-reminder, and idle-nudge triggers.

**Architecture:** Keep the existing Electron `pet / console / settings` window split, keep the Stage 1 settings information architecture intact, and treat this stage as a focused polish + notification-delivery pass instead of a rewrite. The renderer handles navigation, page shells, and daily-vs-deep settings separation; the main process owns system notifications and click-to-focus behavior; the proactive engine remains the trigger source.

**Tech Stack:** Electron, React, TypeScript, Zustand, global CSS (`styles.css`), Vitest, Playwright

---

## File Map

### A1 UI polish leftovers

- Modify: `src/renderer/src/features/pet/DesktopPet.tsx`
  - Remove the extra pet-stage glow layer, keep the improved floating controls, and preserve hover/chat interactions.
- Modify: `src/renderer/src/styles.css`
  - Continue unifying the pet shell, quick controls, console chat room, settings menu, and notification/deep-settings shells under the A1 material language.
- Test: `tests/unit/renderer/desktopPet.spec.tsx`
- Test: `tests/unit/renderer/consoleWindow.spec.tsx`
- Test: `tests/unit/renderer/messageList.spec.tsx`
- Test: `tests/e2e/app-launch.spec.ts`

### Memory Debug relocation

- Modify: `src/renderer/src/App.tsx`
  - Stop wiring Memory Debug into the console surface and instead pass snapshot/refresh handlers into the settings surface.
- Modify: `src/renderer/src/features/console/ConsoleWindow.tsx`
  - Remove the always-present Memory Debug panel from the console body.
- Modify: `src/renderer/src/features/settings/SettingsWindow.tsx`
  - Add a new `memoryDebug` page key and render path.
- Modify: `src/renderer/src/features/settings/SettingsCenterShell.tsx`
  - Add the new nav item while preserving the left-nav architecture.
- Create: `src/renderer/src/features/settings/pages/MemoryDebugSettingsPage.tsx`
  - Wrap the existing debug surface in a full-settings page shell.
- Modify: `src/renderer/src/features/settings/pages/index.ts`
- Modify: `src/renderer/src/features/memory/MemoryDebugPanel.tsx`
  - Keep content behavior, but make the panel work cleanly when embedded inside a settings page rather than a console body slot.
- Modify: `src/renderer/src/i18n.ts`
  - Add `memoryDebugPage`, `memoryDebugSettingsHint`, and any supporting strings needed for the full settings page.
- Test: `tests/unit/renderer/settingsWindow.spec.tsx`
- Test: `tests/unit/renderer/memoryDebugPanel.spec.tsx`
- Test: `tests/unit/renderer/consoleWindow.spec.tsx`
- Test: `tests/e2e/app-launch.spec.ts`

### Notification MVP

- Create: `src/main/systemNotificationService.ts`
  - Centralize app-running notification delivery and notification-click focus behavior.
- Modify: `src/main/index.ts`
  - Instantiate the notification service, expose settings lookups, and connect proactive triggers to notification delivery.
- Modify: `src/main/createWindow.ts`
  - Add a focused “bring the console frontmost” helper that can be reused by notification click handlers.
- Modify: `src/core/proactive/proactiveTriggerEngine.ts`
  - Emit notification-delivery callbacks for `memory.follow_up`, `task.time_reminder`, and `idle_nudge` without changing the existing pet-bubble path.
- Modify: `src/shared/contracts/notificationSettings.ts`
  - Keep the current settings shape unless the implementation proves a tiny additive field is required; prefer staying with the existing contract.
- Modify: `src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx`
  - Make the page explain the “app-running only” boundary clearly.
- Modify: `src/renderer/src/i18n.ts`
  - Add boundary-copy and any small explanatory labels needed for notification support.
- Test: `tests/unit/main/notificationSettingsStore.spec.ts`
- Create: `tests/unit/main/systemNotificationService.spec.ts`
- Modify: `tests/unit/core/proactive/proactiveTriggerEngine.spec.ts`
- Test: `tests/unit/renderer/notificationSettingsPage.spec.tsx`

### Stage docs and verification

- Modify: `tests/e2e/app-launch.spec.ts`
  - Add/update the memory-debug-through-settings path and keep the A1 task/scroll tests aligned.
- Modify: `docs/aurabot-development-status.md`
  - Append the new stage checkpoint and the final verification bundle.

---

### Task 1: Finish the current A1 surface polish checkpoint

**Files:**
- Modify: `src/renderer/src/features/pet/DesktopPet.tsx`
- Modify: `src/renderer/src/styles.css`
- Test: `tests/unit/renderer/desktopPet.spec.tsx`
- Test: `tests/unit/renderer/consoleWindow.spec.tsx`
- Test: `tests/unit/renderer/messageList.spec.tsx`
- Test: `tests/e2e/app-launch.spec.ts`

- [ ] **Step 1: Add a failing assertion that the old decorative pet-stage layer is gone while controls remain reachable**

```tsx
it("keeps pet quick controls reachable without the extra atmosphere layer", async () => {
  await renderPet();

  expect(container.querySelector(".desktop-pet-atmosphere")).toBeNull();
  expect(container.querySelector(".pet-quick-controls")).not.toBeNull();
});
```

- [ ] **Step 2: Run the pet/console renderer tests to verify the assertion fails before cleanup**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/desktopPet.spec.tsx
AssertionError: expected <div class="desktop-pet-atmosphere">…</div> to be null
```

- [ ] **Step 3: Remove the pet-stage atmosphere wrapper and continue the A1 shell polish**

```tsx
// DesktopPet.tsx
<div className="desktop-pet-stage">
  <button className="desktop-pet-model-button" ...>
    ...
  </button>
  <TaskStickyNotes tasks={tasks} displayLanguage={petSettings.displayLanguage} variant="pet" />
</div>
```

```css
.desktop-pet {
  background: transparent;
}

.console-chat {
  padding: 18px;
  border: 1px solid rgba(132, 101, 86, 0.14);
  border-radius: 28px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.9), rgba(255, 248, 242, 0.88)),
    rgba(255, 252, 248, 0.86);
}
```

- [ ] **Step 4: Re-run the renderer tests and the long-conversation e2e path**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx
corepack pnpm test:e2e tests/e2e/app-launch.spec.ts --grep "keeps long conversations pinned in the conversation message list once chat overflows"
```

Expected:

```text
3 passed
1 passed
```

- [ ] **Step 5: Commit the visual-polish checkpoint**

```bash
git add src/renderer/src/features/pet/DesktopPet.tsx src/renderer/src/styles.css tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx tests/e2e/app-launch.spec.ts
git commit -m "feat: continue A1 surface polish"
```

---

### Task 2: Move Memory Debug out of the console body and into full settings

**Files:**
- Modify: `src/renderer/src/App.tsx`
- Modify: `src/renderer/src/features/console/ConsoleWindow.tsx`
- Modify: `src/renderer/src/features/settings/SettingsWindow.tsx`
- Modify: `src/renderer/src/features/settings/SettingsCenterShell.tsx`
- Create: `src/renderer/src/features/settings/pages/MemoryDebugSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/index.ts`
- Modify: `src/renderer/src/features/memory/MemoryDebugPanel.tsx`
- Modify: `src/renderer/src/i18n.ts`
- Test: `tests/unit/renderer/settingsWindow.spec.tsx`
- Test: `tests/unit/renderer/memoryDebugPanel.spec.tsx`
- Test: `tests/unit/renderer/consoleWindow.spec.tsx`
- Test: `tests/e2e/app-launch.spec.ts`

- [ ] **Step 1: Write failing tests for the new settings page and the console removal**

```tsx
it("shows a dedicated Memory Debug page in full settings", async () => {
  await act(async () => {
    root.render(<SettingsWindow {...baseProps} memoryDebugSnapshot={snapshot} onLoadMemoryDebugSnapshot={vi.fn()} />);
  });

  expect(container.textContent).toContain("Memory Debug");
  await act(async () => {
    container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Memory Debug"]')!.click();
  });
  expect(container.textContent).toContain("Last Recall");
});

it("does not render memory debug in the console body anymore", async () => {
  await act(async () => {
    root.render(<ConsoleWindow {...baseProps} />);
  });

  expect(container.textContent).not.toContain("Memory Debug");
});
```

- [ ] **Step 2: Run the failing renderer tests**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/memoryDebugPanel.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/settingsWindow.spec.tsx
FAIL  tests/unit/renderer/consoleWindow.spec.tsx
```

- [ ] **Step 3: Add the new full-settings page and route the snapshot into settings**

```tsx
// SettingsWindow.tsx
type SettingsPageKey = ... | "memoryDebug";

{currentPage === "memoryDebug" ? (
  <MemoryDebugSettingsPage
    petSettings={props.petSettings}
    snapshot={props.memoryDebugSnapshot}
    onRefresh={props.onLoadMemoryDebugSnapshot}
  />
) : null}

// App.tsx
<SettingsWindow
  ...
  memoryDebugSnapshot={memoryDebugSnapshot}
  onLoadMemoryDebugSnapshot={loadMemoryDebugSnapshot}
/>
```

- [ ] **Step 4: Remove `MemoryDebugPanel` from the console body**

```tsx
// ConsoleWindow.tsx
<main className="console-window" ...>
  <header className="console-header">...</header>
  <div className="console-body">
    <section className="console-chat" ...>
      <MessageList ... />
      <Composer ... />
    </section>
    <TaskStickyNotes ... />
  </div>
</main>
```

- [ ] **Step 5: Add the new settings-page copy and update e2e to find Memory Debug in full settings**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/memoryDebugPanel.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx
corepack pnpm test:e2e tests/e2e/app-launch.spec.ts --grep "memory debug|settings"
```

Expected:

```text
Renderer tests passed
Relevant e2e passed
```

- [ ] **Step 6: Commit the Memory Debug relocation**

```bash
git add src/renderer/src/App.tsx src/renderer/src/features/console/ConsoleWindow.tsx src/renderer/src/features/settings/SettingsWindow.tsx src/renderer/src/features/settings/SettingsCenterShell.tsx src/renderer/src/features/settings/pages/MemoryDebugSettingsPage.tsx src/renderer/src/features/settings/pages/index.ts src/renderer/src/features/memory/MemoryDebugPanel.tsx src/renderer/src/i18n.ts tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/memoryDebugPanel.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/e2e/app-launch.spec.ts
git commit -m "feat: move memory debug into full settings"
```

---

### Task 3: Ship app-running system notifications for follow-up, time-reminder, and idle-nudge triggers

**Files:**
- Create: `src/main/systemNotificationService.ts`
- Modify: `src/main/index.ts`
- Modify: `src/main/createWindow.ts`
- Modify: `src/core/proactive/proactiveTriggerEngine.ts`
- Modify: `src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx`
- Modify: `src/renderer/src/i18n.ts`
- Test: `tests/unit/main/notificationSettingsStore.spec.ts`
- Create: `tests/unit/main/systemNotificationService.spec.ts`
- Modify: `tests/unit/core/proactive/proactiveTriggerEngine.spec.ts`
- Test: `tests/unit/renderer/notificationSettingsPage.spec.tsx`

- [ ] **Step 1: Add failing tests for notification delivery gating**

```ts
it("delivers follow-up notifications only when the matching switches are enabled", async () => {
  const delivered: Array<{ title: string; body: string }> = [];
  const service = new SystemNotificationService({
    isSupported: () => true,
    createNotification: (payload) => delivered.push(payload),
    openConsoleWindow: vi.fn()
  });

  service.deliver({
    kind: "memory_follow_up",
    message: "提醒你一下，今晚的算法面试别忘了。",
    settings: {
      systemNotificationsEnabled: true,
      permissionState: "system_managed",
      followUpNotificationsEnabled: true,
      timeReminderNotificationsEnabled: true,
      idleNudgeNotificationsEnabled: false
    }
  });

  expect(delivered).toHaveLength(1);
});
```

- [ ] **Step 2: Run the main/proactive/notification tests and confirm the new service is missing**

Run:

```bash
corepack pnpm vitest run tests/unit/main/notificationSettingsStore.spec.ts tests/unit/core/proactive/proactiveTriggerEngine.spec.ts tests/unit/renderer/notificationSettingsPage.spec.tsx tests/unit/main/systemNotificationService.spec.ts
```

Expected:

```text
FAIL  tests/unit/main/systemNotificationService.spec.ts
Error: Cannot find module "../../../src/main/systemNotificationService"
```

- [ ] **Step 3: Create the main-process notification delivery service**

```ts
export type SystemNotificationKind = "memory_follow_up" | "task_reminder" | "proactive_idle";

export class SystemNotificationService {
  deliver(input: {
    kind: SystemNotificationKind;
    message: string;
    settings: NotificationSettings;
  }): boolean {
    if (!this.dependencies.isSupported()) return false;
    if (!input.settings.systemNotificationsEnabled) return false;
    if (input.kind === "memory_follow_up" && !input.settings.followUpNotificationsEnabled) return false;
    if (input.kind === "task_reminder" && !input.settings.timeReminderNotificationsEnabled) return false;
    if (input.kind === "proactive_idle" && !input.settings.idleNudgeNotificationsEnabled) return false;
    ...
  }
}
```

- [ ] **Step 4: Wire the proactive engine into the notification service**

```ts
// ProactiveTriggerEngineDependencies
deliverSystemNotification?(input: { kind: PetActivity["kind"]; message: string }): void;

// after emitActivity(...)
this.dependencies.deliverSystemNotification?.({
  kind: activity.kind,
  message: activity.message
});
```

- [ ] **Step 5: Make notification clicks focus or open the console**

```ts
// createWindow.ts
export function focusConsoleWindow(): BrowserWindow {
  return openConsoleWindow();
}

// systemNotificationService.ts
notification.on("click", () => {
  this.dependencies.openConsoleWindow();
});
```

- [ ] **Step 6: Update the Notification settings page to state the app-running-only boundary clearly**

```tsx
<SettingsPageLead
  title={t(displayLanguage, "notifications")}
  hint={t(displayLanguage, "notificationSettingsHint")}
/>
<p className="settings-page-boundary-note">
  {t(displayLanguage, "notificationAppRunningOnlyHint")}
</p>
```

- [ ] **Step 7: Re-run the notification/proactive tests**

Run:

```bash
corepack pnpm vitest run tests/unit/main/notificationSettingsStore.spec.ts tests/unit/main/systemNotificationService.spec.ts tests/unit/core/proactive/proactiveTriggerEngine.spec.ts tests/unit/renderer/notificationSettingsPage.spec.tsx
corepack pnpm typecheck
```

Expected:

```text
All notification/proactive tests passed
Type check passed
```

- [ ] **Step 8: Commit the notification MVP**

```bash
git add src/main/systemNotificationService.ts src/main/index.ts src/main/createWindow.ts src/core/proactive/proactiveTriggerEngine.ts src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx src/renderer/src/i18n.ts tests/unit/main/notificationSettingsStore.spec.ts tests/unit/main/systemNotificationService.spec.ts tests/unit/core/proactive/proactiveTriggerEngine.spec.ts tests/unit/renderer/notificationSettingsPage.spec.tsx
git commit -m "feat: add app-running system notifications"
```

---

### Task 4: Final verification and status-log refresh

**Files:**
- Modify: `tests/e2e/app-launch.spec.ts`
- Modify: `docs/aurabot-development-status.md`

- [ ] **Step 1: Add/update e2e coverage for the moved Memory Debug path and the notification-facing UI**

```ts
test("opens memory debug from full settings instead of the console body", async () => {
  ...
});
```

- [ ] **Step 2: Run the stage verification bundle**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/memoryDebugPanel.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx tests/unit/main/notificationSettingsStore.spec.ts tests/unit/main/systemNotificationService.spec.ts tests/unit/core/proactive/proactiveTriggerEngine.spec.ts tests/unit/core/voice/voiceAdapter.spec.ts tests/unit/core/tasks/taskPresentation.spec.ts tests/integration/orchestration/chat-task.spec.ts
corepack pnpm typecheck
corepack pnpm build
corepack pnpm test:e2e
```

Expected:

```text
Targeted unit tests passed
Type check passed
Build passed
All e2e passed
```

- [ ] **Step 3: Update `aurabot-development-status.md` with a new stage checkpoint**

```md
## 2026-05-08 UI polish + notifications checkpoint

1. 控制台正文已移除常驻 Memory Debug，深层调试改为完整设置中的独立页。
2. 系统通知 MVP 已覆盖 follow_up、time_reminder、idle_nudge 的 App 运行中投递。
3. 点击通知会聚焦 AuraBot 控制台，形成统一回流路径。
4. 本轮 UI 收口继续沿用 A1 材质，完成桌宠控件、控制台和设置菜单余量收紧。
```

- [ ] **Step 4: Commit the verification/docs checkpoint**

```bash
git add tests/e2e/app-launch.spec.ts docs/aurabot-development-status.md
git commit -m "docs: record UI polish and notifications checkpoint"
```

---

## Recommended Build Order

1. Task 1: stabilize the current A1 polish changes already sitting in the working tree
2. Task 2: move Memory Debug out of the console into full settings
3. Task 3: add app-running system notifications
4. Task 4: full verification and status-log refresh

This order keeps the UI hierarchy clean before wiring new notification behavior into the same user-facing surfaces.

## Acceptance Checklist

- [ ] The pet shell no longer relies on the removed decorative atmosphere layer and the current A1 controls still work.
- [ ] The console body no longer renders Memory Debug inline.
- [ ] Full settings contains a dedicated `Memory Debug` page.
- [ ] Quick settings remains a daily-use layer, not a debug layer.
- [ ] Follow-up, time-reminder, and idle-nudge triggers can deliver app-running system notifications.
- [ ] Clicking a notification focuses or opens the console.
- [ ] The notifications page clearly states the “app-running only” boundary.
- [ ] The final verification bundle (`vitest` + `typecheck` + `build` + `test:e2e`) passes.

## Notes for Execution

1. Keep `docs/settings-center-stage1-spec.md` untouched; it is user-owned and currently untracked.
2. Do not reopen the architecture of task runners, memory storage, or model roles during this stage.
3. Prefer adding one main-process notification service rather than scattering `Notification` calls across `index.ts`, `createWindow.ts`, and the proactive engine.
4. If macOS notification behavior differs under Playwright or Electron test mode, contain those differences inside unit tests and main-process abstractions instead of weakening the product code path.
