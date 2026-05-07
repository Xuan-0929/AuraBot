# AuraBot A1 UI Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the A1 `Daily Cozy` UI pass so AuraBot feels like a companion-first desktop pet, with chat as the primary interaction and tasks demoted into P2-style sticky notes instead of dashboard panels.

**Architecture:** Keep the current Electron window split (`pet` / `console` / `settings`) and the Stage 1 settings information architecture intact. Implement the A1 pass mostly in renderer code through new cozy theme tokens, one reusable sticky-note task surface, and copy updates, while only touching core task presentation where current user-facing text still leaks runner or system tone.

**Tech Stack:** Electron, React, TypeScript, Zustand, global CSS (`styles.css`), Vitest, Playwright

---

## File Map

### Renderer shell and theme

- Modify: `src/renderer/src/i18n.ts`
  - Update shell copy, settings intro copy, sticky-note labels, and softer chat/status strings for `zh` and `en`.
- Modify: `src/renderer/src/styles.css`
  - Add A1 theme tokens, warm window backgrounds, sticky-note styling, cozy settings cards, and calmer console layout rules.
- Modify: `src/renderer/src/App.tsx`
  - Pass `tasks` into the desktop pet surface so the pet can render sticky notes.

### Desktop pet

- Modify: `src/renderer/src/features/pet/DesktopPet.tsx`
  - Render a small read-only sticky-note cluster beside the Live2D model, keep the pet as the primary visual center, and preserve existing hover/chat behavior.
- Create: `src/renderer/src/features/tasks/TaskStickyNotes.tsx`
  - Shared renderer component for P2 sticky-note task summaries. Supports `pet` and `console` variants, keeps task copy light, and preserves subtle console-side cancel/delete actions.

### Console

- Modify: `src/renderer/src/features/console/ConsoleWindow.tsx`
  - Replace the old task overlay with the new sticky-note surface, keep the message stream as the main center, and soften the console shell hierarchy.
- Modify: `src/renderer/src/features/chat/MessageList.tsx`
  - Refresh the empty-state copy and keep the conversation view aligned with the quieter A1 chat-room feel.
- Delete: `src/renderer/src/features/tasks/TaskStatusOverlay.tsx`
  - Remove the old two-column task panel once the sticky-note component fully replaces it.

### Settings center

- Create: `src/renderer/src/features/settings/SettingsPageLead.tsx`
  - Tiny shared section-lead component so every settings page can present soft product copy consistently.
- Modify: `src/renderer/src/features/settings/SettingsWindow.tsx`
  - Update the window subtitle and overall A1 header treatment.
- Modify: `src/renderer/src/features/settings/SettingsCenterShell.tsx`
  - Keep the left-nav structure but switch to a softer “small room / notebook” shell.
- Modify: `src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/ModelSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx`
  - Add page lead copy and update visual grouping without changing Stage 1 information architecture.

### Core companion-facing text

- Modify: `src/core/tasks/taskPresentation.ts`
  - Soften result, queued, running, and failed task messages so they match the companion-first tone and stop sounding like a runner status feed.

### Tests

- Modify: `tests/unit/renderer/desktopPet.spec.tsx`
- Create: `tests/unit/renderer/taskStickyNotes.spec.tsx`
- Modify: `tests/unit/renderer/consoleWindow.spec.tsx`
- Delete: `tests/unit/renderer/taskStatusOverlay.spec.tsx`
- Modify: `tests/unit/renderer/settingsWindow.spec.tsx`
- Modify: `tests/unit/renderer/personaSettingsPage.spec.tsx`
- Modify: `tests/unit/renderer/notificationSettingsPage.spec.tsx`
- Modify: `tests/unit/core/tasks/taskPresentation.spec.ts`
- Modify: `tests/e2e/app-launch.spec.ts`
  - Cover the A1 copy, sticky-note rendering, and the settings/console surface continuity.

### Progress docs

- Modify: `docs/aurabot-development-status.md`
  - Record the A1 UI pass once the implementation and verification are complete.

---

### Task 1: Freeze the A1 shell copy and cozy theme tokens

**Files:**
- Modify: `src/renderer/src/i18n.ts`
- Modify: `src/renderer/src/styles.css`
- Test: `tests/unit/renderer/consoleWindow.spec.tsx`
- Test: `tests/unit/renderer/settingsWindow.spec.tsx`

- [ ] **Step 1: Write the failing shell-copy assertions**

```tsx
it("uses the A1 Chinese copy in the console shell", async () => {
  await act(async () => {
    root.render(<ConsoleWindow {...baseProps} />);
  });

  expect(container.textContent).toContain("想认真聊的时候，我们就在这里慢慢说。");
  expect(container.textContent).toContain("我在，今天想先从哪件事开始？");
});

it("uses the A1 settings subtitle in English", async () => {
  await act(async () => {
    root.render(<SettingsWindow {...baseProps} />);
  });

  expect(container.textContent).toContain("Shape Aura into the kind of companion you want beside you.");
});
```

- [ ] **Step 2: Run the renderer shell tests and confirm they fail on the old copy**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/consoleWindow.spec.tsx
FAIL  tests/unit/renderer/settingsWindow.spec.tsx
AssertionError: expected "桌宠需要完整对话线程时，在这里继续聊。" to contain "想认真聊的时候，我们就在这里慢慢说。"
```

- [ ] **Step 3: Update the shell copy in `i18n.ts`**

```ts
zh: {
  consoleSubtitle: "想认真聊的时候，我们就在这里慢慢说。",
  readyWhenYouAre: "我在，今天想先从哪件事开始？",
  productSettingsSubtitle: "把 Aura 调成更像你喜欢的陪伴方式。",
  taskStickyNotes: "今日便签",
  taskStickyNoteHint: "我先替你轻轻记在旁边。"
},
en: {
  consoleSubtitle: "When you want to talk properly, we can settle in here.",
  readyWhenYouAre: "I'm here. What feels easiest to start with today?",
  productSettingsSubtitle: "Shape Aura into the kind of companion you want beside you.",
  taskStickyNotes: "Today's notes",
  taskStickyNoteHint: "I'll keep the important bits lightly pinned beside us."
}
```

- [ ] **Step 4: Add A1 theme tokens at the top of `styles.css` and swap the base font stack**

```css
:root {
  --aura-warm-bg: #fffaf5;
  --aura-paper: #f6ece3;
  --aura-paper-strong: rgba(255, 255, 255, 0.92);
  --aura-paper-soft: rgba(255, 252, 248, 0.82);
  --aura-ink: #5d463d;
  --aura-muted: #8a6d61;
  --aura-border: rgba(132, 101, 86, 0.16);
  --aura-shadow: 0 18px 40px rgba(123, 96, 81, 0.12);
  color: var(--aura-ink);
  background: transparent;
  font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "SF Pro Text", sans-serif;
}
```

- [ ] **Step 5: Re-run the shell tests and confirm the new copy is live**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx
```

Expected:

```text
PASS  tests/unit/renderer/consoleWindow.spec.tsx
PASS  tests/unit/renderer/settingsWindow.spec.tsx
Test Files  2 passed
```

- [ ] **Step 6: Commit the shell-copy/token checkpoint**

```bash
git add src/renderer/src/i18n.ts src/renderer/src/styles.css tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx
git commit -m "feat: add A1 cozy shell copy and theme tokens"
```

---

### Task 2: Add P2 sticky notes to the desktop pet without stealing focus from the model

**Files:**
- Modify: `src/renderer/src/App.tsx`
- Modify: `src/renderer/src/features/pet/DesktopPet.tsx`
- Create: `src/renderer/src/features/tasks/TaskStickyNotes.tsx`
- Modify: `src/renderer/src/styles.css`
- Test: `tests/unit/renderer/desktopPet.spec.tsx`
- Create: `tests/unit/renderer/taskStickyNotes.spec.tsx`

- [ ] **Step 1: Write the failing sticky-note tests first**

```tsx
it("shows at most two sticky notes in pet mode and strips command prefixes", () => {
  const tasks = [
    makeTask({ id: "task-1", title: "帮我写周报。", status: "running" }),
    makeTask({ id: "task-2", title: "晚上提醒我回消息。", kind: "time_reminder", status: "queued" }),
    makeTask({ id: "task-3", title: "帮我整理文档。", status: "queued" })
  ];

  render(<TaskStickyNotes tasks={tasks} variant="pet" displayLanguage="zh" />);

  expect(screen.getByLabelText("今日便签")).toBeVisible();
  expect(screen.getByText("写周报")).toBeVisible();
  expect(screen.getByText("晚上提醒我回消息")).toBeVisible();
  expect(screen.queryByText("整理文档")).toBeNull();
});

it("renders the pet sticky note beside the model surface", async () => {
  await renderPet({
    tasks: [makeTask({ id: "task-1", title: "帮我写周报。", status: "running" })]
  });

  expect(container.querySelector(".desktop-pet-sticky-notes")).not.toBeNull();
  expect(container.textContent).toContain("写周报");
});
```

- [ ] **Step 2: Run the pet and sticky-note tests to verify the component is missing**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/taskStickyNotes.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/taskStickyNotes.spec.tsx
Error: Cannot find module "../../../src/renderer/src/features/tasks/TaskStickyNotes"
```

- [ ] **Step 3: Create `TaskStickyNotes.tsx` with a pet-safe summarizer**

```tsx
import type { Task } from "../../../../shared/schemas/task";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { t } from "../../i18n";

export function TaskStickyNotes({
  tasks,
  displayLanguage,
  variant,
  onCancelTask,
  onDeleteTask
}: {
  tasks: Task[];
  displayLanguage: PetSettings["displayLanguage"];
  variant: "pet" | "console";
  onCancelTask?: (taskId: string) => void;
  onDeleteTask?: (taskId: string) => void;
}) {
  const visibleTasks = pickVisibleTasks(tasks, variant);

  if (visibleTasks.length === 0) {
    return null;
  }

  return (
    <aside className={`task-sticky-notes task-sticky-notes-${variant}`} aria-label={t(displayLanguage, "taskStickyNotes")}>
      <header className="task-sticky-notes-header">
        <strong>{t(displayLanguage, "taskStickyNotes")}</strong>
        <span>{t(displayLanguage, "taskStickyNoteHint")}</span>
      </header>
      {visibleTasks.map((task) => (
        <article key={task.id} className="task-sticky-note">
          <strong>{summarizeTitle(task.title)}</strong>
          <span>{summarizeMeta(task, displayLanguage)}</span>
          {variant === "console" ? (
            <div className="task-sticky-note-actions">
              {isActive(task.status) ? (
                <button type="button" onClick={() => onCancelTask?.(task.id)}>
                  {t(displayLanguage, "cancel")}
                </button>
              ) : null}
              {isSettled(task.status) ? (
                <button type="button" onClick={() => onDeleteTask?.(task.id)}>
                  {t(displayLanguage, "delete")}
                </button>
              ) : null}
            </div>
          ) : null}
        </article>
      ))}
    </aside>
  );
}

function pickVisibleTasks(tasks: Task[], variant: "pet" | "console"): Task[] {
  if (variant === "pet") {
    return tasks
      .filter((task) => task.status === "queued" || task.status === "running" || task.status === "waiting")
      .slice(0, 2);
  }

  return tasks.slice(0, 3);
}

function isActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

function isSettled(status: Task["status"]): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}
```

- [ ] **Step 4: Thread the sticky notes into `App.tsx` and `DesktopPet.tsx`**

```tsx
// App.tsx
<DesktopPet
  agentStatus={agentStatus}
  tasks={tasks}
  modelPath={live2dModelState?.currentModelPath}
  petSettings={petSettings}
  petActivity={petActivity}
  petCursorPosition={petCursorPosition}
  debugPetOverlay={debugPetOverlay}
  onOpenConsole={openConsoleWindow}
  onOpenSettings={openSettingsWindow}
  onSendVoiceInput={(content) => sendMessage(content, "pet_voice")}
  onSendTextInput={(content) => sendMessage(content, "pet_text")}
  onSetMousePassthrough={setPetMousePassthrough}
  onMovePetWindowBy={movePetWindowBy}
  onShowPetNotice={showPetNotice}
  onClearPetActivity={clearPetActivity}
/>

// DesktopPet.tsx
export interface DesktopPetProps {
  agentStatus: AgentStatus;
  tasks: Task[];
  modelPath?: string;
  petSettings: PetSettings;
  petActivity?: PetActivity;
  petCursorPosition?: PetCursorPosition;
  debugPetOverlay: boolean;
  onOpenConsole(): Promise<void>;
  onOpenSettings(): Promise<void>;
  onSendVoiceInput(content: string): Promise<void>;
  onSendTextInput(content: string): Promise<void>;
  onSetMousePassthrough(enabled: boolean): Promise<void>;
  onMovePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  onShowPetNotice(message: string): void;
  onClearPetActivity(id?: string): void;
}

<div className="desktop-pet-stage">
  <button
    ref={modelButtonRef}
    type="button"
    className="desktop-pet-model-button"
    aria-label={t(petSettings.displayLanguage, "interactWithAuraBot")}
    onPointerEnter={handleModelPointerEnter}
    onPointerLeave={handleModelPointerLeave}
  >
    <div className="desktop-pet-model-shell">
      <Live2DModelCanvas
        modelPath={modelPath ?? DEFAULT_MODEL_PATH}
        agentStatus={agentStatus}
        debugOverlay={debugPetOverlay}
        cursorPosition={petCursorPosition}
      />
    </div>
  </button>
  <TaskStickyNotes tasks={tasks} displayLanguage={petSettings.displayLanguage} variant="pet" />
</div>
```

- [ ] **Step 5: Add the pet-side sticky-note styles without overpowering the model**

```css
.desktop-pet-stage {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
}

.desktop-pet-sticky-notes,
.task-sticky-notes-pet {
  position: absolute;
  right: 10px;
  bottom: 26px;
  width: min(132px, 34vw);
}

.task-sticky-note {
  padding: 10px 11px;
  border: 1px solid var(--aura-border);
  border-radius: 16px;
  background: rgba(255, 250, 244, 0.78);
  box-shadow: var(--aura-shadow);
}
```

- [ ] **Step 6: Re-run the pet tests**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/taskStickyNotes.spec.tsx
```

Expected:

```text
PASS  tests/unit/renderer/desktopPet.spec.tsx
PASS  tests/unit/renderer/taskStickyNotes.spec.tsx
```

- [ ] **Step 7: Commit the desktop-pet sticky-note checkpoint**

```bash
git add src/renderer/src/App.tsx src/renderer/src/features/pet/DesktopPet.tsx src/renderer/src/features/tasks/TaskStickyNotes.tsx src/renderer/src/styles.css tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/taskStickyNotes.spec.tsx
git commit -m "feat: add P2 sticky notes to the desktop pet"
```

---

### Task 3: Turn the console into a quiet chat room and retire the old task overlay

**Files:**
- Modify: `src/renderer/src/features/console/ConsoleWindow.tsx`
- Modify: `src/renderer/src/features/chat/MessageList.tsx`
- Modify: `src/renderer/src/styles.css`
- Delete: `src/renderer/src/features/tasks/TaskStatusOverlay.tsx`
- Modify: `tests/unit/renderer/consoleWindow.spec.tsx`
- Delete: `tests/unit/renderer/taskStatusOverlay.spec.tsx`

- [ ] **Step 1: Replace the old overlay expectation with a sticky-note expectation**

```tsx
it("renders sticky notes instead of the old task overlay in the console", async () => {
  await act(async () => {
    root.render(
      <ConsoleWindow
        {...baseProps}
        tasks={[makeTask({ id: "task-1", title: "帮我写周报。", status: "running" })]}
      />
    );
  });

  expect(container.querySelector(".task-status-overlay")).toBeNull();
  expect(container.querySelector(".task-sticky-notes-console")).not.toBeNull();
  expect(container.textContent).toContain("写周报");
});
```

- [ ] **Step 2: Run the console renderer tests and verify they fail**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/consoleWindow.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/consoleWindow.spec.tsx
AssertionError: expected null not to be null
```

- [ ] **Step 3: Replace `TaskStatusOverlay` with `TaskStickyNotes` inside `ConsoleWindow.tsx`**

```tsx
import { TaskStickyNotes } from "../tasks/TaskStickyNotes";

export function ConsoleWindow({
  agentStatus,
  messages,
  tasks,
  petSettings,
  appConfig,
  memoryDebugSnapshot,
  onUpdatePetSettings,
  onOpenSettings,
  onLoadMemoryDebugSnapshot,
  onSend,
  onCancelTask,
  onDeleteTask
}: ConsoleWindowProps) {
  return (
    <main className="console-window" aria-label={t(petSettings.displayLanguage, "consoleTitle")}>
      <header className="console-header">
        <div>
          <h1>{t(petSettings.displayLanguage, "consoleTitle")}</h1>
          <p>{t(petSettings.displayLanguage, "consoleSubtitle")}</p>
        </div>
        <div className="console-header-tools">
          <AgentStatusBar status={agentStatus} displayLanguage={petSettings.displayLanguage} />
          <PetSettingsPanel
            settings={petSettings}
            appConfig={appConfig}
            onUpdate={onUpdatePetSettings}
            onOpenSettings={onOpenSettings}
          />
        </div>
      </header>
      <MemoryDebugPanel
        snapshot={memoryDebugSnapshot}
        expanded={memoryDebugExpanded}
        displayLanguage={petSettings.displayLanguage}
        onToggle={() => setMemoryDebugExpanded((current) => !current)}
        onRefresh={onLoadMemoryDebugSnapshot}
      />
      <div className="console-body">
        <section className="console-chat" aria-label={t(petSettings.displayLanguage, "mainConversation")}>
          <MessageList messages={messages} displayLanguage={petSettings.displayLanguage} />
          <Composer displayLanguage={petSettings.displayLanguage} onSend={onSend} />
        </section>
        <TaskStickyNotes
          tasks={tasks}
          displayLanguage={petSettings.displayLanguage}
          variant="console"
          onCancelTask={onCancelTask}
          onDeleteTask={onDeleteTask}
        />
      </div>
    </main>
  );
}
```

- [ ] **Step 4: Soften the empty state and console layout in `MessageList.tsx` and `styles.css`**

```tsx
if (messages.length === 0) {
  return (
    <div className="empty-state">
      <h1>{t(displayLanguage, "appTitle")}</h1>
      <p>{t(displayLanguage, "readyWhenYouAre")}</p>
    </div>
  );
}
```

```css
.console-body {
  flex: 1 1 auto;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(220px, 280px);
  gap: 18px;
  min-height: 0;
}

.console-window {
  background:
    radial-gradient(circle at 14% 10%, rgba(255, 246, 235, 0.88), transparent 26%),
    radial-gradient(circle at 86% 10%, rgba(255, 251, 247, 0.78), transparent 24%),
    linear-gradient(180deg, #fffaf5 0%, #f4ebe3 100%);
}
```

- [ ] **Step 5: Remove the obsolete overlay component and its test**

```bash
rm src/renderer/src/features/tasks/TaskStatusOverlay.tsx
rm tests/unit/renderer/taskStatusOverlay.spec.tsx
```

- [ ] **Step 6: Re-run the console renderer tests**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx
```

Expected:

```text
PASS  tests/unit/renderer/consoleWindow.spec.tsx
PASS  tests/unit/renderer/messageList.spec.tsx
```

- [ ] **Step 7: Commit the console chat-room checkpoint**

```bash
git add src/renderer/src/features/console/ConsoleWindow.tsx src/renderer/src/features/chat/MessageList.tsx src/renderer/src/styles.css tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/messageList.spec.tsx
git rm src/renderer/src/features/tasks/TaskStatusOverlay.tsx tests/unit/renderer/taskStatusOverlay.spec.tsx
git commit -m "feat: turn the console into a quiet A1 chat room"
```

---

### Task 4: Re-skin the settings center as a small companion room without changing Stage 1 structure

**Files:**
- Create: `src/renderer/src/features/settings/SettingsPageLead.tsx`
- Modify: `src/renderer/src/features/settings/SettingsWindow.tsx`
- Modify: `src/renderer/src/features/settings/SettingsCenterShell.tsx`
- Modify: `src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/ModelSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx`
- Modify: `src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx`
- Modify: `src/renderer/src/i18n.ts`
- Modify: `src/renderer/src/styles.css`
- Test: `tests/unit/renderer/settingsWindow.spec.tsx`
- Test: `tests/unit/renderer/personaSettingsPage.spec.tsx`
- Test: `tests/unit/renderer/notificationSettingsPage.spec.tsx`

- [ ] **Step 1: Write failing tests for the cozy settings intros**

```tsx
it("renders the softer settings subtitle and general-page lead copy", async () => {
  await act(async () => {
    root.render(<SettingsWindow {...baseProps} petSettings={{ ...baseProps.petSettings, displayLanguage: "zh" }} />);
  });

  expect(container.textContent).toContain("把 Aura 调成更像你喜欢的陪伴方式。");
  expect(container.textContent).toContain("一些全局的小习惯，就从这里慢慢调。");
});
```

- [ ] **Step 2: Run the settings tests and confirm the new copy is absent**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/personaSettingsPage.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx
```

Expected:

```text
FAIL  tests/unit/renderer/settingsWindow.spec.tsx
AssertionError: expected "AuraBot 的产品级配置。" to contain "把 Aura 调成更像你喜欢的陪伴方式。"
```

- [ ] **Step 3: Create a shared `SettingsPageLead.tsx`**

```tsx
export function SettingsPageLead({
  title,
  description
}: {
  title: string;
  description: string;
}) {
  return (
    <header className="settings-page-lead">
      <h2>{title}</h2>
      <p>{description}</p>
    </header>
  );
}
```

- [ ] **Step 4: Use the shared lead component in the settings pages**

```tsx
// GeneralSettingsPage.tsx
<section className="settings-page-section" aria-label={t(displayLanguage, "general")}>
  <SettingsPageLead
    title={t(displayLanguage, "general")}
    description={t(displayLanguage, "generalSettingsHint")}
  />
  <label className="settings-page-field">
    <span>{t(displayLanguage, "language")}</span>
    <select
      value={petSettings.displayLanguage}
      aria-label={t(displayLanguage, "language")}
      onChange={(event) =>
        void onUpdatePetSettings({
          displayLanguage: event.currentTarget.value as PetSettings["displayLanguage"]
        })
      }
    >
      <option value="en">{t(displayLanguage, "english")}</option>
      <option value="zh">{t(displayLanguage, "chinese")}</option>
    </select>
  </label>
</section>

// NotificationSettingsPage.tsx
<SettingsPageLead
  title={t(displayLanguage, "notifications")}
  description={t(displayLanguage, "notificationSettingsHint")}
/>
```

- [ ] **Step 5: Add the page-lead copy keys and cozy sidebar/card styles**

```ts
zh: {
  generalSettingsHint: "一些全局的小习惯，就从这里慢慢调。",
  petBehaviorSettingsHint: "决定 Aura 平时怎么陪着你待在桌面上。",
  modelSettingsHint: "模型还是这些模型，只是我把说明说得更像人话一点。",
  notificationSettingsHint: "决定我什么时候可以轻轻提醒你一下。"
}
```

```css
.settings-center-sidebar {
  border-radius: 18px;
  background: rgba(255, 251, 247, 0.92);
  box-shadow: var(--aura-shadow);
}

.settings-page-lead h2 {
  margin: 0 0 6px;
  color: var(--aura-ink);
  font-size: 16px;
}

.settings-page-lead p {
  margin: 0;
  color: var(--aura-muted);
  line-height: 1.55;
}
```

- [ ] **Step 6: Re-run the settings tests**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/personaSettingsPage.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx
```

Expected:

```text
PASS  tests/unit/renderer/settingsWindow.spec.tsx
PASS  tests/unit/renderer/personaSettingsPage.spec.tsx
PASS  tests/unit/renderer/notificationSettingsPage.spec.tsx
```

- [ ] **Step 7: Commit the settings-center A1 pass**

```bash
git add src/renderer/src/features/settings/SettingsPageLead.tsx src/renderer/src/features/settings/SettingsWindow.tsx src/renderer/src/features/settings/SettingsCenterShell.tsx src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx src/renderer/src/features/settings/pages/ModelSettingsPage.tsx src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx src/renderer/src/i18n.ts src/renderer/src/styles.css tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/personaSettingsPage.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx
git commit -m "feat: productize settings center with A1 cozy styling"
```

---

### Task 5: Soften task-result and task-status language so it matches the companion-first UI

**Files:**
- Modify: `src/core/tasks/taskPresentation.ts`
- Test: `tests/unit/core/tasks/taskPresentation.spec.ts`

- [ ] **Step 1: Update the task-presentation spec with the new companion tone**

```ts
it("uses a companion-style queued reply", () => {
  const response = buildTaskStatusResponse({
    ...baseTask,
    status: "queued",
    title: "帮我做一个 GitHub 周报。",
    latestProgress: "Task queued from main chat."
  });

  expect(response).toBe("这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。");
});
```

- [ ] **Step 2: Run the task-presentation tests and verify the old copy fails**

Run:

```bash
corepack pnpm vitest run tests/unit/core/tasks/taskPresentation.spec.ts
```

Expected:

```text
FAIL  tests/unit/core/tasks/taskPresentation.spec.ts
AssertionError: expected "我还没开始处理..." to be "这件事我先替你记在旁边了..."
```

- [ ] **Step 3: Update the hard-coded task copy in `taskPresentation.ts`**

```ts
if (task.status === "queued") {
  return "这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。";
}

if (task.status === "failed") {
  return {
    content: `这次我先卡在：${cleanTaskResultText(getLatestMeaningfulSummary(task, events))}。你要是愿意，我们接着一起改。`,
    attachments: []
  };
}

return `这件事我已经在弄了，最近一步是：${latestSummary}\n弄好后我会回来跟你说。`;
```

- [ ] **Step 4: Re-run the task-presentation tests**

Run:

```bash
corepack pnpm vitest run tests/unit/core/tasks/taskPresentation.spec.ts
```

Expected:

```text
PASS  tests/unit/core/tasks/taskPresentation.spec.ts
```

- [ ] **Step 5: Commit the task-copy checkpoint**

```bash
git add src/core/tasks/taskPresentation.ts tests/unit/core/tasks/taskPresentation.spec.ts
git commit -m "feat: soften task presentation copy for A1"
```

---

### Task 6: Run the full verification bundle, refresh the progress doc, and leave a clean handoff

**Files:**
- Modify: `tests/e2e/app-launch.spec.ts`
- Modify: `docs/aurabot-development-status.md`

- [ ] **Step 1: Update the e2e expectations to the A1 shell copy**

```ts
await expect(consoleWindow.getByRole("main", { name: "AuraBot Console" })).toContainText(
  "想认真聊的时候，我们就在这里慢慢说。"
);
await expect(settingsWindow.getByRole("main", { name: "Settings" })).toContainText(
  "把 Aura 调成更像你喜欢的陪伴方式。"
);
```

- [ ] **Step 2: Run the targeted e2e suite**

Run:

```bash
corepack pnpm test:e2e -- --grep "launches as a small desktop pet|opens the product settings window directly from pet quick controls"
```

Expected:

```text
2 passed
```

- [ ] **Step 3: Run the full regression bundle**

Run:

```bash
corepack pnpm vitest run tests/unit/renderer/desktopPet.spec.tsx tests/unit/renderer/taskStickyNotes.spec.tsx tests/unit/renderer/consoleWindow.spec.tsx tests/unit/renderer/settingsWindow.spec.tsx tests/unit/renderer/personaSettingsPage.spec.tsx tests/unit/renderer/notificationSettingsPage.spec.tsx tests/unit/core/tasks/taskPresentation.spec.ts
corepack pnpm typecheck
corepack pnpm build
corepack pnpm test:e2e
```

Expected:

```text
All targeted unit tests passed
Type check passed
Build completed successfully
Playwright: all tests passed
```

- [ ] **Step 4: Update the development status doc with the A1 checkpoint**

```md
## 2026-05-07 A1 UI 产品化 checkpoint

1. 桌宠默认体验切到 A1 `Daily Cozy`，任务默认以便签式附属位呈现。
2. 控制台从任务面板形态收成安静聊天室，消息成为第一主角。
3. 设置中心保留 Stage 1 结构，但统一切到柔和小屋式产品语言。
4. 任务反馈文案继续保留真实信息，但不再像 runner 状态广播。
```

- [ ] **Step 5: Commit the verification + docs checkpoint**

```bash
git add tests/e2e/app-launch.spec.ts docs/aurabot-development-status.md
git commit -m "docs: record A1 UI productization checkpoint"
```

---

## Recommended Build Order

1. Task 1: lock copy and theme tokens first
2. Task 2: add sticky notes to the pet before touching the console
3. Task 3: convert the console after the sticky-note component exists
4. Task 4: restyle settings center with the new shared visual language
5. Task 5: update task-response copy so the UI and spoken text match
6. Task 6: verification, docs, and handoff

This order keeps the first visible surface stable before expanding the same language to the console and settings pages.

## Acceptance Checklist

- [ ] The pet window still opens as the default surface and keeps the Live2D model as the clear visual center.
- [ ] The pet shows only a tiny P2 sticky-note cluster, never a dashboard-like task panel.
- [ ] The console no longer renders the old two-column task overlay.
- [ ] The console empty state and subtitle match the A1 calm-companion tone.
- [ ] The settings window still uses the Stage 1 page structure but no longer reads like a backend control panel.
- [ ] Task result, queued, running, and failed copy all sound like Aura talking, not a runner log.
- [ ] `vitest`, `typecheck`, `build`, and `test:e2e` all pass before merge.

## Notes for Execution

1. Do not re-open the CLI autodiscovery discussion during this phase. Keep model/task-runner behavior functionally intact and only reframe how it is presented.
2. Avoid inventing a new settings architecture. The left-nav shell from Stage 1 is already the correct structural base.
3. If any single renderer file starts taking more than one concern, split it instead of letting the A1 pass become a CSS dump.
4. Prefer small commits after each task. The visual surface will change a lot; frequent checkpoints make review much easier.
