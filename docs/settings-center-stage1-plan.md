# Settings Center Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first productized Settings Center for AuraBot so existing scattered settings are consolidated into a stable left-nav settings experience, while introducing persona editing, local Live2D model switching, and notification settings as first-class product surfaces.

**Architecture:** Keep the current Electron main/preload/renderer split, but refactor the settings UI from one large page into a shell plus page modules. Add thin shared contracts for persona settings, Live2D model discovery, and notification settings, and keep persistence and filesystem concerns in main/core. This phase does not introduce heavy editors; it establishes a stable structure that later UI polish, notification delivery, and Live2D state-machine work can build on.

**Tech Stack:** Electron, React, TypeScript, Zustand, Zod, existing IPC contracts, existing settings window infrastructure

---

## 1. Scope

This phase is **Settings Center consolidation**, not full product polish.

### In scope

1. Refactor the settings UI into a left-nav settings center.
2. Move current settings into clearer pages.
3. Add persona settings with:
   - default light editor
   - expandable semi-pro editor
4. Add a Live2D model page with:
   - current model preview
   - current model metadata
   - local model discovery
   - model switching
5. Add a notification settings page with light configuration only.
6. Preserve and extend existing model settings and pet behavior settings.

### Explicitly out of scope

1. Full Live2D motion/expression mapping editor.
2. Full-text soul/prompt editor.
3. Heavy WYSIWYG preview studio.
4. Full notification workflow builder.
5. Final visual polish pass across the whole app.

## 2. Product Structure

The Settings Center should be split into six pages:

1. `General`
2. `Pet Behavior`
3. `Model`
4. `Persona`
5. `Live2D Model`
6. `Notifications`

### Page boundaries

#### General

- display language
- config file entry points
- lightweight product-level info

#### Pet Behavior

- click-through
- pet bubble behavior
- cursor follow behavior
- hover/interaction behavior

#### Model

- provider selection
- model health state
- test connection
- current model summary
- config file shortcut

#### Persona

Default surface:

- persona name
- short persona description
- tone
- relationship feel
- proactive level

Expandable semi-pro surface:

- principles
- taboos
- reminder style
- reply preference

#### Live2D Model

- preview current model
- show current model metadata
- list local switchable models
- switch active model

#### Notifications

- system notification master switch
- permission status
- whether follow-ups use system notifications
- whether reminder tasks use system notifications
- whether idle nudges may notify

## 3. Recommended Build Order

Implement in this order:

1. Settings shell and page split
2. Move existing settings into the new pages
3. Add persona settings contracts and page
4. Add Live2D model contracts, discovery, and page
5. Add notification settings contracts and page
6. Final integration pass and tests

Why this order:

1. It stabilizes information architecture before visual polish.
2. It prevents future settings from being re-added into one oversized `SettingsWindow.tsx`.
3. It gives later work on notifications and Live2D state-machine a proper configuration home.

## 4. File Map

### Renderer shell and pages

- Modify: [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)
- Create: [src/renderer/src/features/settings/SettingsCenterShell.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsCenterShell.tsx)
- Create: [src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/ModelSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/ModelSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx)

### Shared contracts and schemas

- Modify: [src/shared/contracts/ipc.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/ipc.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
- Create: [src/shared/contracts/personaSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/personaSettings.ts)
- Create: [src/shared/contracts/live2dModel.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/live2dModel.ts)
- Create: [src/shared/contracts/notificationSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/notificationSettings.ts)
- Create: [src/shared/schemas/personaSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/personaSettings.ts)
- Create: [src/shared/schemas/live2dModel.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/live2dModel.ts)
- Create: [src/shared/schemas/notificationSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/notificationSettings.ts)

### Main and core services

- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/main/index.ts](/Users/xuan/Documents/AuraBot/src/main/index.ts)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Modify: [src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)
- Create: [src/core/live2d/modelRegistry.ts](/Users/xuan/Documents/AuraBot/src/core/live2d/modelRegistry.ts)
- Create: [src/core/profiles/personaSettingsService.ts](/Users/xuan/Documents/AuraBot/src/core/profiles/personaSettingsService.ts)
- Create: [src/main/notificationSettingsStore.ts](/Users/xuan/Documents/AuraBot/src/main/notificationSettingsStore.ts)

### Existing files likely reused

- Reuse: [src/main/petSettingsStore.ts](/Users/xuan/Documents/AuraBot/src/main/petSettingsStore.ts)
- Reuse: [src/core/profiles/soulService.ts](/Users/xuan/Documents/AuraBot/src/core/profiles/soulService.ts)
- Reuse: [src/renderer/src/i18n.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/i18n.ts)
- Reuse: [src/renderer/src/styles.css](/Users/xuan/Documents/AuraBot/src/renderer/src/styles.css)

### Tests

- Modify: [tests/unit/renderer/settingsWindow.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsWindow.spec.tsx)
- Create: [tests/unit/renderer/settingsCenterShell.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsCenterShell.spec.tsx)
- Create: [tests/unit/renderer/personaSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/personaSettingsPage.spec.tsx)
- Create: [tests/unit/renderer/live2dModelSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/live2dModelSettingsPage.spec.tsx)
- Create: [tests/unit/renderer/notificationSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/notificationSettingsPage.spec.tsx)
- Create: [tests/unit/core/live2d/modelRegistry.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/live2d/modelRegistry.spec.ts)
- Create: [tests/unit/core/profiles/personaSettingsService.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/profiles/personaSettingsService.spec.ts)
- Modify: [tests/e2e/app-launch.spec.ts](/Users/xuan/Documents/AuraBot/tests/e2e/app-launch.spec.ts)

## 5. Tasks

### Task 1: Split the current Settings window into a shell plus pages

**Files:**
- Modify: [src/renderer/src/features/settings/SettingsWindow.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsWindow.tsx)
- Create: [src/renderer/src/features/settings/SettingsCenterShell.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsCenterShell.tsx)
- Create: [src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/GeneralSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/PetBehaviorSettingsPage.tsx)
- Create: [src/renderer/src/features/settings/pages/ModelSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/ModelSettingsPage.tsx)
- Test: [tests/unit/renderer/settingsCenterShell.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsCenterShell.spec.tsx)

- [ ] Write a failing renderer test that expects a left navigation shell with six tabs.
- [ ] Verify the test fails because the current settings UI is still a single page.
- [ ] Extract the current layout into `SettingsCenterShell.tsx`.
- [ ] Move existing language/config/model/task/proactive/pet behavior controls into the appropriate initial pages.
- [ ] Run the renderer shell tests again and confirm they pass.

### Task 2: Stabilize page-level navigation state

**Files:**
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Modify: [src/renderer/src/App.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/App.tsx)
- Modify: [src/renderer/src/features/settings/SettingsCenterShell.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsCenterShell.tsx)
- Test: [tests/unit/renderer/settingsCenterShell.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsCenterShell.spec.tsx)

- [ ] Add a small renderer-side state model for current settings page selection.
- [ ] Keep this state local to the settings experience; do not overload main chat store state with unrelated editor logic.
- [ ] Verify keyboard/mouse switching between pages works.
- [ ] Verify the current page content changes without remounting the whole app shell.

### Task 3: Add persona settings contracts and persistence mapping

**Files:**
- Create: [src/shared/contracts/personaSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/personaSettings.ts)
- Create: [src/shared/schemas/personaSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/personaSettings.ts)
- Create: [src/core/profiles/personaSettingsService.ts](/Users/xuan/Documents/AuraBot/src/core/profiles/personaSettingsService.ts)
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Create: [src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/PersonaSettingsPage.tsx)
- Test: [tests/unit/core/profiles/personaSettingsService.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/profiles/personaSettingsService.spec.ts)
- Test: [tests/unit/renderer/personaSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/personaSettingsPage.spec.tsx)

- [ ] Define a light editor contract:
  - name
  - short persona description
  - tone
  - relationship style
  - proactive level
- [ ] Define a semi-pro contract:
  - principles
  - taboos
  - reminder style
  - reply preference
- [ ] Map those fields into the current soul/persona storage structure without exposing raw prompt text.
- [ ] Add a failing service test that expects these fields to round-trip to the stored profile/soul layer.
- [ ] Add a failing renderer test that expects:
  - light editor shown by default
  - advanced section hidden by default
  - advanced section visible after clicking the expand button
- [ ] Implement the page and pass the tests.

### Task 4: Add Live2D model registry and model switching page

**Files:**
- Create: [src/shared/contracts/live2dModel.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/live2dModel.ts)
- Create: [src/shared/schemas/live2dModel.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/live2dModel.ts)
- Create: [src/core/live2d/modelRegistry.ts](/Users/xuan/Documents/AuraBot/src/core/live2d/modelRegistry.ts)
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/main/index.ts](/Users/xuan/Documents/AuraBot/src/main/index.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Create: [src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/Live2DModelSettingsPage.tsx)
- Test: [tests/unit/core/live2d/modelRegistry.spec.ts](/Users/xuan/Documents/AuraBot/tests/unit/core/live2d/modelRegistry.spec.ts)
- Test: [tests/unit/renderer/live2dModelSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/live2dModelSettingsPage.spec.tsx)

- [ ] Add a registry that scans the local model folder and returns:
  - model id
  - display name
  - model path
  - preview-capable metadata
- [ ] Add a current-model getter and setter.
- [ ] Write a failing test that expects local models to be listed from the configured folder.
- [ ] Add a page that shows:
  - current model preview area
  - current model metadata
  - switchable local model list
- [ ] Verify switching the model updates stored config and current UI state.

### Task 5: Add notification settings contracts and a lightweight page

**Files:**
- Create: [src/shared/contracts/notificationSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/contracts/notificationSettings.ts)
- Create: [src/shared/schemas/notificationSettings.ts](/Users/xuan/Documents/AuraBot/src/shared/schemas/notificationSettings.ts)
- Create: [src/main/notificationSettingsStore.ts](/Users/xuan/Documents/AuraBot/src/main/notificationSettingsStore.ts)
- Modify: [src/main/registerIpc.ts](/Users/xuan/Documents/AuraBot/src/main/registerIpc.ts)
- Modify: [src/preload/index.ts](/Users/xuan/Documents/AuraBot/src/preload/index.ts)
- Modify: [src/renderer/src/app/store.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/app/store.ts)
- Create: [src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/pages/NotificationSettingsPage.tsx)
- Test: [tests/unit/renderer/notificationSettingsPage.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/notificationSettingsPage.spec.tsx)

- [ ] Keep this page intentionally light.
- [ ] Expose:
  - system notification enabled
  - permission status
  - follow-up notification toggle
  - reminder-task notification toggle
  - idle-nudge notification toggle
- [ ] Do not implement notification orchestration policy editing in this phase.
- [ ] Add a failing renderer test for the expected controls, then implement the page.

### Task 6: Unify settings-page styles and navigation polish

**Files:**
- Modify: [src/renderer/src/styles.css](/Users/xuan/Documents/AuraBot/src/renderer/src/styles.css)
- Modify: [src/renderer/src/i18n.ts](/Users/xuan/Documents/AuraBot/src/renderer/src/i18n.ts)
- Modify: [src/renderer/src/features/settings/SettingsCenterShell.tsx](/Users/xuan/Documents/AuraBot/src/renderer/src/features/settings/SettingsCenterShell.tsx)
- Test: [tests/unit/renderer/settingsWindow.spec.tsx](/Users/xuan/Documents/AuraBot/tests/unit/renderer/settingsWindow.spec.tsx)

- [ ] Add consistent nav styles, page section hierarchy, and card spacing.
- [ ] Keep this as structural polish, not the full later UI beautification phase.
- [ ] Add i18n strings for new page names and new page-level actions.
- [ ] Verify the settings page still renders correctly in both English and Chinese.

### Task 7: Expand end-to-end coverage

**Files:**
- Modify: [tests/e2e/app-launch.spec.ts](/Users/xuan/Documents/AuraBot/tests/e2e/app-launch.spec.ts)

- [ ] Add one e2e path that opens the settings center and verifies:
  - left nav is visible
  - model page still works
  - persona page can expand advanced editing
  - Live2D model page shows at least the current model
- [ ] Keep the e2e assertions surface-level; do not try to exhaustively validate every field in one test.

## 6. Acceptance Criteria

This phase is complete only if:

1. The settings UI is no longer one oversized page.
2. Existing settings are redistributed into the new page structure without regressions.
3. Persona settings support light editing plus expandable semi-pro editing.
4. Live2D model settings can preview and switch local models.
5. Notification settings exist as a real page with saveable light controls.
6. The information architecture is stable enough that later UI beautification does not require a structural rewrite.

## 7. Verification Plan

Minimum verification before claiming this phase complete:

1. `corepack pnpm typecheck`
2. `corepack pnpm test:unit`
3. Targeted tests for:
   - settings shell
   - persona settings page
   - live2d model registry/page
   - notification settings page
4. `corepack pnpm build`
5. At least one settings-focused e2e path

## 8. Risks to Watch

1. `SettingsWindow.tsx` is already carrying too much; if the shell split is only cosmetic, the refactor will fail to buy future clarity.
2. Persona settings can easily drift into “full prompt editor” scope creep.
3. Live2D local model discovery can get messy if the accepted model folder structure is not made explicit early.
4. Notification settings can accidentally turn into notification delivery implementation; keep delivery work separate from settings-surface work.

## 9. Recommended Immediate Next Step

Start with **Task 1: split the settings shell** before touching persona, Live2D model switching, or notifications. If the shell and page boundaries are not stable, every later settings feature will be more expensive to land.
