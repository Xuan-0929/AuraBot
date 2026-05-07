// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { SettingsWindowProps } from "../../../src/renderer/src/features/settings/SettingsWindow";
import { SettingsWindow } from "../../../src/renderer/src/features/settings/SettingsWindow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps: SettingsWindowProps = {
  profile: {
    id: "profile-1",
    displayName: "Aura",
    active: true,
    createdAt: "2026-05-04T00:00:00.000Z",
    updatedAt: "2026-05-04T00:00:00.000Z",
    soul: {
      id: "soul-1",
      profileId: "profile-1",
      name: "Aura",
      persona: "会陪我聊天，也会顺手帮我推进事情。",
      tone: "轻松、短句、自然",
      proactiveLevel: "balanced",
      principles: ["先把事情接住。"],
      taboos: ["不要像客服一样说话。"],
      reminderStyle: "",
      replyPreference: "",
      speakingStyle: "自然、短句",
      relationship: "像熟悉的长期搭子",
      boundaries: [],
      topicPreferences: [],
      createdAt: "2026-05-04T00:00:00.000Z",
      updatedAt: "2026-05-04T00:00:00.000Z"
    }
  },
  petSettings: {
    clickThroughPet: false,
    displayLanguage: "en"
  },
  appConfig: {
    chatModel: {
      provider: "openai-compatible",
      model: "gpt-5.4",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
      hasApiKey: true
    },
    gateModel: {
      provider: "openai-compatible",
      model: "gate-model",
      enabled: false,
      hasApiKey: false
    },
    memoryModel: {
      provider: "openai-compatible",
      model: "memory-model",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
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
  },
  modelHealth: {
    chatModel: {
      role: "chatModel",
      provider: "openai-compatible",
      enabled: true,
      configured: true,
      hasApiKey: true,
      model: "gpt-5.4",
      baseUrl: "https://api.example.com/v1",
      state: "connected"
    },
    gateModel: {
      role: "gateModel",
      provider: "openai-compatible",
      enabled: false,
      configured: false,
      hasApiKey: false,
      model: "gate-model",
      state: "not_configured"
    },
    memoryModel: {
      role: "memoryModel",
      provider: "openai-compatible",
      enabled: true,
      configured: true,
      hasApiKey: true,
      model: "memory-model",
      baseUrl: "https://api.example.com/v1",
      state: "configured"
    }
  },
  live2dModelState: {
    currentModelId: "hiyori",
    currentModelPath: "assets/live2d/hiyori/Hiyori.model3.json",
    models: [
      {
        id: "hiyori",
        displayName: "Hiyori",
        folderName: "hiyori",
        manifestFileName: "Hiyori.model3.json",
        manifestPath: "assets/live2d/hiyori/Hiyori.model3.json"
      }
    ]
  },
  notificationSettings: {
    systemNotificationsEnabled: true,
    permissionState: "system_managed",
    followUpNotificationsEnabled: true,
    timeReminderNotificationsEnabled: true,
    idleNudgeNotificationsEnabled: false
  },
  onUpdatePetSettings: vi.fn(async () => undefined),
  onUpdateTaskRunnerSettings: vi.fn(async () => undefined),
  onUpdateModelProviderSettings: vi.fn(async () => undefined),
  onUpdateProactiveSettings: vi.fn(async () => undefined),
  onUpdatePersonaSettings: vi.fn(async () => undefined),
  onSetCurrentLive2DModel: vi.fn(async () => undefined),
  onUpdateNotificationSettings: vi.fn(async () => undefined),
  onOpenConfigFile: vi.fn(async () => undefined),
  onTestModelConnection: vi.fn(async () => undefined)
};

describe("SettingsCenterShell", () => {
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

  it("renders a six-tab settings center shell", async () => {
    await act(async () => {
      root.render(<SettingsWindow {...baseProps} />);
    });

    const tabs = Array.from(container.querySelectorAll('[role="tab"]')).map((tab) => tab.textContent?.trim());

    expect(tabs).toEqual(["General", "Pet Behavior", "Model", "Persona", "Live2D Model", "Notifications"]);
  });

  it("switches visible page content from the left navigation without remounting the shell", async () => {
    await act(async () => {
      root.render(<SettingsWindow {...baseProps} />);
    });

    expect(container.textContent).toContain("Product-level configuration for AuraBot.");
    expect(container.textContent).toContain("Language");
    expect(container.textContent).not.toContain("Click through character");

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Pet Behavior"]')!.click();
    });

    expect(container.textContent).toContain("Click through character");
    expect(container.textContent).not.toContain("API key saved");

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Model"]')!.click();
    });

    expect(container.textContent).toContain("API key saved");
    expect(container.textContent).toContain("Test connection");
  });
});
