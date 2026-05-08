// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsWindow, type SettingsWindowProps } from "../../../src/renderer/src/features/settings/SettingsWindow";
import type { MemoryDebugSnapshot } from "../../../src/shared/contracts/ipc";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const stylesheetText = readFileSync(resolve(process.cwd(), "src/renderer/src/styles.css"), "utf8");

const memoryDebugSnapshot: MemoryDebugSnapshot = {
  generatedAt: "2026-05-07T12:00:00.000Z",
  nodes: [
    {
      id: "memory_preference_hotpot",
      profileId: "profile-1",
      type: "preference",
      state: "active",
      content: "用户喜欢火锅",
      importance: 0.82,
      createdAt: "2026-05-07T11:00:00.000Z",
      updatedAt: "2026-05-07T11:00:00.000Z"
    }
  ],
  edges: [],
  summaries: [],
  recentEvents: [
    {
      id: "event-memory-1",
      eventType: "memory.write_completed",
      payload: {},
      createdAt: "2026-05-07T11:00:30.000Z"
    }
  ],
  lastRecallTrace: {
    query: "火锅那件事我该记着什么？",
    recallPlan: {
      queryTerms: ["火锅"],
      targetTypes: ["preference"],
      rankedNodeIds: ["memory_preference_hotpot"]
    },
    seedNodeIds: ["memory_preference_hotpot"],
    expandedNodeIds: [],
    returnedNodeIds: ["memory_preference_hotpot"],
    traversedEdges: []
  }
};

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
      runners: [
        {
          id: "codex",
          label: "Codex",
          enabled: true,
          priority: 1,
          command: "/usr/local/bin/codex",
          workingDirectory: "/Users/xuan/project",
          timeoutMs: 300000
        },
        {
          id: "claude",
          label: "Claude Code",
          enabled: false,
          priority: 2,
          command: "/opt/homebrew/bin/claude",
          timeoutMs: 300000
        },
        {
          id: "hermes",
          label: "Hermes",
          enabled: false,
          priority: 3,
          command: "hermes",
          timeoutMs: 300000
        }
      ]
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
      state: "connected",
      lastCheckedAt: "2026-05-06T12:00:00.000Z"
    },
    gateModel: {
      role: "gateModel",
      provider: "openai-compatible",
      enabled: false,
      configured: false,
      hasApiKey: false,
      model: "gate-model",
      state: "not_configured",
      lastErrorCode: "missing_configuration"
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
  memoryDebugSnapshot,
  onUpdatePetSettings: vi.fn(async () => undefined),
  onUpdateTaskRunnerSettings: vi.fn(async () => undefined),
  onUpdateModelProviderSettings: vi.fn(async () => undefined),
  onUpdateProactiveSettings: vi.fn(async () => undefined),
  onUpdatePersonaSettings: vi.fn(async () => undefined),
  onSetCurrentLive2DModel: vi.fn(async () => undefined),
  onUpdateNotificationSettings: vi.fn(async () => undefined),
  onLoadMemoryDebugSnapshot: vi.fn(async () => undefined),
  onOpenConfigFile: vi.fn(async () => undefined),
  onTestModelConnection: vi.fn(async () => undefined)
};

describe("SettingsWindow", () => {
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

  it("renders the product settings surface with model, language, and character controls", async () => {
    const onUpdatePetSettings = vi.fn(async () => undefined);
    const onOpenConfigFile = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onUpdatePetSettings={onUpdatePetSettings}
          onOpenConfigFile={onOpenConfigFile}
        />
      );
    });

    expect(container.querySelector<HTMLElement>('[aria-label="Settings"]')?.textContent).toContain("Settings");
    expect(container.textContent).toContain("General");
    expect(container.textContent).toContain("Pet Behavior");
    expect(container.textContent).toContain("Model");
    expect(container.textContent).toContain("Persona");
    expect(container.textContent).toContain("Live2D Model");
    expect(container.textContent).toContain("Notifications");
    expect(container.querySelector<HTMLSelectElement>('[aria-label="Language"]')?.value).toBe("en");

    await act(async () => {
      const language = container.querySelector<HTMLSelectElement>('[aria-label="Language"]')!;
      language.value = "zh";
      language.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Pet Behavior"]')!.click();
    });
    await act(async () => {
      container.querySelector<HTMLInputElement>('[aria-label="Click through character"]')!.click();
    });

    expect(onUpdatePetSettings).toHaveBeenCalledWith({ displayLanguage: "zh" });
    expect(onUpdatePetSettings).toHaveBeenCalledWith({ clickThroughPet: true });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Model"]')!.click();
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>(".settings-config-button")!.click();
    });

    expect(onOpenConfigFile).toHaveBeenCalledTimes(1);
  });

  it("updates proactive wait timing from the settings page", async () => {
    const onUpdateProactiveSettings = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onUpdateProactiveSettings={onUpdateProactiveSettings}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Notifications"]')!.click();
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

  it("renders Memory Debug as a dedicated full-settings page", async () => {
    const onLoadMemoryDebugSnapshot = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onLoadMemoryDebugSnapshot={onLoadMemoryDebugSnapshot}
        />
      );
    });

    expect(container.textContent).toContain("Memory Debug");

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Memory Debug"]')!.click();
    });

    expect(container.textContent).toContain("Last Recall");
    expect(container.textContent).toContain("火锅那件事我该记着什么？");
    expect(onLoadMemoryDebugSnapshot).toHaveBeenCalledTimes(1);
  });

  it("moves task CLI controls into their own settings page and lets users adjust timeout limits", async () => {
    const onUpdateTaskRunnerSettings = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onUpdateTaskRunnerSettings={onUpdateTaskRunnerSettings}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Model"]')!.click();
    });

    expect(container.textContent).not.toContain("Task CLI settings");

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Task CLI"]')!.click();
    });

    expect(container.querySelector<HTMLElement>('[aria-label="Task CLI settings"]')).not.toBeNull();
    expect(container.querySelector<HTMLInputElement>('[aria-label="Codex task time limit minutes"]')?.value).toBe("5");

    await act(async () => {
      container.querySelector<HTMLInputElement>('[aria-label="Enable Claude Code task runner"]')!.click();
    });

    expect(onUpdateTaskRunnerSettings).toHaveBeenCalledWith([
      expect.objectContaining({
        id: "codex",
        enabled: true,
        priority: 1,
        timeoutMs: 300000
      }),
      expect.objectContaining({
        id: "claude",
        enabled: true,
        priority: 2,
        timeoutMs: 300000
      }),
      expect.objectContaining({
        id: "hermes",
        enabled: false,
        priority: 3,
        timeoutMs: 300000
      })
    ]);

    await act(async () => {
      const hermesPriority = container.querySelector<HTMLInputElement>('[aria-label="Hermes task runner priority"]')!;
      hermesPriority.value = "1";
      hermesPriority.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(onUpdateTaskRunnerSettings).toHaveBeenLastCalledWith([
      expect.objectContaining({
        id: "codex",
        priority: 1,
        timeoutMs: 300000
      }),
      expect.objectContaining({
        id: "claude",
        priority: 2,
        timeoutMs: 300000
      }),
      expect.objectContaining({
        id: "hermes",
        priority: 1,
        timeoutMs: 300000
      })
    ]);

    await act(async () => {
      const codexTimeout = container.querySelector<HTMLInputElement>('[aria-label="Codex task time limit minutes"]')!;
      codexTimeout.value = "12";
      codexTimeout.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(onUpdateTaskRunnerSettings).toHaveBeenLastCalledWith([
      expect.objectContaining({
        id: "codex",
        timeoutMs: 720000
      }),
      expect.objectContaining({
        id: "claude",
        timeoutMs: 300000
      }),
      expect.objectContaining({
        id: "hermes",
        timeoutMs: 300000
      })
    ]);
  });

  it("updates model providers from the settings page", async () => {
    const onUpdateModelProviderSettings = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onUpdateModelProviderSettings={onUpdateModelProviderSettings}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Model"]')!.click();
    });

    await act(async () => {
      const gateProvider = container.querySelector<HTMLSelectElement>('[aria-label="Gate provider"]')!;
      expect(Array.from(gateProvider.options).map((option) => option.value)).toEqual([
        "openai-compatible",
        "langchain-openai"
      ]);
      gateProvider.value = "langchain-openai";
      gateProvider.dispatchEvent(new Event("change", { bubbles: true }));
    });

    expect(onUpdateModelProviderSettings).toHaveBeenCalledWith({
      role: "gateModel",
      provider: "langchain-openai"
    });
  });

  it("tests model connectivity from the settings page", async () => {
    const onTestModelConnection = vi.fn(async () => undefined);

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          onTestModelConnection={onTestModelConnection}
        />
      );
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[role="tab"][aria-label="Model"]')!.click();
    });

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[aria-label="Test Chat connection"]')!.click();
    });

    expect(onTestModelConnection).toHaveBeenCalledWith("chatModel");
  });

  it("localizes the settings surface when Chinese is selected", async () => {
    await act(async () => {
      root.render(<SettingsWindow {...baseProps} />);
    });

    const englishSubtitle =
      container.querySelector<HTMLElement>(".settings-window-copy p")?.textContent ?? "";
    const englishLead = container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent ?? "";

    await act(async () => {
      root.render(
        <SettingsWindow
          {...baseProps}
          petSettings={{
            clickThroughPet: false,
            displayLanguage: "zh"
          }}
        />
      );
    });

    const localizedSubtitle =
      container.querySelector<HTMLElement>(".settings-window-copy p")?.textContent ?? "";
    const localizedLead = container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent ?? "";

    expect(container.querySelector<HTMLElement>(".settings-window-header h1")?.textContent).toBe("设置");
    expect(container.querySelector<HTMLElement>(".settings-page-lead h2")?.textContent).toBe("通用");
    expect(container.textContent).toContain("桌宠行为");
    expect(container.textContent).toContain("模型");
    expect(container.textContent).toContain("人格");
    expect(container.textContent).toContain("Live2D 模型");
    expect(container.textContent).toContain("通知");
    expect(localizedSubtitle).not.toBe("");
    expect(localizedLead).not.toBe("");
    expect(localizedSubtitle).not.toBe(englishSubtitle);
    expect(localizedLead).not.toBe(englishLead);
    expect(container.textContent).not.toContain("Product-level configuration");
    expect(container.textContent).not.toContain("Click through character");
  });

  it("renders the settings header and general lead structure in English", async () => {
    await act(async () => {
      root.render(<SettingsWindow {...baseProps} />);
    });

    expect(container.querySelector<HTMLElement>(".settings-window-kicker")?.textContent).toBeTruthy();
    expect(container.querySelector<HTMLElement>(".settings-window-header h1")?.textContent).toBe("Settings");
    expect(container.querySelector<HTMLElement>(".settings-window-copy p")?.textContent).toContain("Aura");
    expect(container.querySelectorAll(".settings-page-lead")).toHaveLength(1);
    expect(container.querySelector<HTMLElement>(".settings-page-lead h2")?.textContent).toBe("General");
    expect(container.querySelector<HTMLElement>(".settings-page-lead p")?.textContent).toBeTruthy();
    expect(container.textContent).toContain("Language");
  });

  it("wires the A1 tokens and stable lead/tab contracts into the settings shell styles", () => {
    expect(stylesheetText).toMatch(/\.app-shell-settings\s*{[^}]*background:\s*var\(--aura-warm-bg\);/s);
    expect(stylesheetText).toMatch(/\.console-window\s*{[^}]*color:\s*var\(--aura-ink\);[^}]*var\(--aura-warm-bg\)/s);
    expect(stylesheetText).toMatch(/\.settings-window\s*{[^}]*var\(--aura-warm-bg\)/s);
    expect(stylesheetText).toMatch(/\.settings-center-sidebar\s*{[^}]*border:\s*1px solid var\(--aura-border\);[^}]*border-radius:\s*28px;[^}]*box-shadow:\s*var\(--aura-shadow\);[^}]*backdrop-filter:\s*blur\(14px\);/s);
    expect(stylesheetText).toMatch(/\.settings-window-copy\s*{[^}]*border-radius:\s*30px;[^}]*backdrop-filter:\s*blur\(16px\);/s);
    expect(stylesheetText).toMatch(/\.settings-center-content\s*{[^}]*border-radius:\s*32px;/s);
    expect(stylesheetText).toMatch(/\.settings-page-section\s+\.settings-page-lead\s*>\s*h2\s*{/s);
    expect(stylesheetText).toMatch(/\.settings-page-section\s+\.settings-page-lead\s*>\s*p\s*{/s);
    expect(stylesheetText).toMatch(
      /\.settings-center-tab\[data-active="true"\]:hover\s*{[^}]*rgba\(255,\s*251,\s*247,\s*0\.96\)[^}]*var\(--aura-paper\)/s
    );
  });
});
