// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NotificationSettingsPage } from "../../../src/renderer/src/features/settings/pages/NotificationSettingsPage";
import type { NotificationSettings } from "../../../src/shared/contracts/notificationSettings";
import type { PublicAppConfig } from "../../../src/shared/contracts/settings";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const appConfig: PublicAppConfig = {
  chatModel: {
    provider: "openai-compatible",
    model: "chat-model",
    enabled: true,
    hasApiKey: true
  },
  gateModel: {
    provider: "openai-compatible",
    model: "gate-model",
    enabled: true,
    hasApiKey: true
  },
  memoryModel: {
    provider: "openai-compatible",
    model: "memory-model",
    enabled: true,
    hasApiKey: true
  },
  taskRunner: {
    runners: []
  },
  proactive: {
    idleThresholdMs: 120000,
    idleCooldownMs: 1800000,
    followUpLookaheadMs: 0,
    pollIntervalMs: 10000
  }
};

const notificationSettings: NotificationSettings = {
  systemNotificationsEnabled: true,
  permissionState: "system_managed",
  followUpNotificationsEnabled: true,
  timeReminderNotificationsEnabled: true,
  idleNudgeNotificationsEnabled: false
};

describe("NotificationSettingsPage", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders notification toggles, permission summary, and proactive wait settings", async () => {
    await act(async () => {
      root.render(
        <NotificationSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          appConfig={appConfig}
          notificationSettings={notificationSettings}
          onUpdateNotificationSettings={vi.fn(async () => undefined)}
          onUpdateProactiveSettings={vi.fn(async () => undefined)}
        />
      );
    });

    expect(container.textContent).toContain("Notifications");
    expect(container.textContent).toContain("Choose which little taps on the shoulder Aura is allowed to send.");
    expect(container.textContent).toContain("System notifications");
    expect(container.textContent).toContain("Permission");
    expect(container.textContent).toContain("Managed by system");
    expect(container.textContent).toContain("Follow-up reminders");
    expect(container.textContent).toContain("Time reminder tasks");
    expect(container.textContent).toContain("Idle nudges");
  });

  it("updates notification toggles and proactive wait time", async () => {
    const onUpdateNotificationSettings = vi.fn(async () => undefined);
    const onUpdateProactiveSettings = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <NotificationSettingsPage
          petSettings={{ clickThroughPet: false, displayLanguage: "en" }}
          appConfig={appConfig}
          notificationSettings={notificationSettings}
          onUpdateNotificationSettings={onUpdateNotificationSettings}
          onUpdateProactiveSettings={onUpdateProactiveSettings}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLInputElement>('[aria-label="Enable idle nudge notifications"]')!.click();
    });

    expect(onUpdateNotificationSettings).toHaveBeenCalledWith({
      idleNudgeNotificationsEnabled: true
    });

    await act(async () => {
      const waitMinutes = container.querySelector<HTMLInputElement>('[aria-label="Proactive message wait minutes"]')!;
      waitMinutes.value = "5";
      waitMinutes.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(onUpdateProactiveSettings).toHaveBeenCalledWith({
      idleThresholdMs: 300000
    });
  });
});
