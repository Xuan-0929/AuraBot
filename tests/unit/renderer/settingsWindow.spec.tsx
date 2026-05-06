// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsWindow, type SettingsWindowProps } from "../../../src/renderer/src/features/settings/SettingsWindow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps: SettingsWindowProps = {
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
          workingDirectory: "/Users/xuan/project"
        },
        {
          id: "claude",
          label: "Claude Code",
          enabled: false,
          priority: 2,
          command: "/opt/homebrew/bin/claude"
        },
        {
          id: "hermes",
          label: "Hermes",
          enabled: false,
          priority: 3,
          command: "hermes"
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
  onUpdatePetSettings: vi.fn(async () => undefined),
  onUpdateTaskRunnerSettings: vi.fn(async () => undefined),
  onUpdateModelProviderSettings: vi.fn(async () => undefined),
  onUpdateProactiveSettings: vi.fn(async () => undefined),
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
    expect(container.textContent).toContain("Model");
    expect(container.textContent).toContain("Chat");
    expect(container.textContent).toContain("gpt-5.4");
    expect(container.textContent).toContain("API key saved");
    expect(container.textContent).toContain("Connected");
    expect(container.textContent).toContain("Not configured");
    expect(container.textContent).toContain("Test connection");
    expect(container.textContent).toContain("Character");
    expect(container.textContent).toContain("Task CLI");
    expect(container.textContent).toContain("Proactive messages");
    expect(container.textContent).not.toContain("Local fallback");
    expect(container.querySelector<HTMLInputElement>('[aria-label="Proactive message wait minutes"]')?.value).toBe("2");
    expect(container.textContent).toContain("Codex");
    expect(container.textContent).toContain("Claude Code");
    expect(container.textContent).toContain("Hermes");
    expect(container.querySelector<HTMLSelectElement>('[aria-label="Language"]')?.value).toBe("en");

    await act(async () => {
      const language = container.querySelector<HTMLSelectElement>('[aria-label="Language"]')!;
      language.value = "zh";
      language.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await act(async () => {
      container.querySelector<HTMLInputElement>('[aria-label="Click through character"]')!.click();
    });

    expect(onUpdatePetSettings).toHaveBeenCalledWith({ displayLanguage: "zh" });
    expect(onUpdatePetSettings).toHaveBeenCalledWith({ clickThroughPet: true });

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
      const waitMinutes = container.querySelector<HTMLInputElement>('[aria-label="Proactive message wait minutes"]')!;
      waitMinutes.value = "5";
      waitMinutes.dispatchEvent(new Event("input", { bubbles: true }));
    });

    expect(onUpdateProactiveSettings).toHaveBeenCalledWith({
      idleThresholdMs: 300000
    });
  });

  it("updates task CLI switches and priority values from the settings page", async () => {
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
      container.querySelector<HTMLInputElement>('[aria-label="Enable Claude Code task runner"]')!.click();
    });

    expect(onUpdateTaskRunnerSettings).toHaveBeenCalledWith([
      expect.objectContaining({
        id: "codex",
        enabled: true,
        priority: 1
      }),
      expect.objectContaining({
        id: "claude",
        enabled: true,
        priority: 2
      }),
      expect.objectContaining({
        id: "hermes",
        enabled: false,
        priority: 3
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
        priority: 1
      }),
      expect.objectContaining({
        id: "claude",
        priority: 2
      }),
      expect.objectContaining({
        id: "hermes",
        priority: 1
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
      container.querySelector<HTMLButtonElement>('[aria-label="Test Chat connection"]')!.click();
    });

    expect(onTestModelConnection).toHaveBeenCalledWith("chatModel");
  });

  it("localizes the settings surface when Chinese is selected", async () => {
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

    expect(container.textContent).toContain("设置");
    expect(container.textContent).toContain("通用");
    expect(container.textContent).toContain("模型");
    expect(container.textContent).toContain("任务 CLI");
    expect(container.textContent).toContain("主动消息");
    expect(container.textContent).toContain("打开 config 文件");
    expect(container.textContent).toContain("人物");
    expect(container.textContent).toContain("穿透人物点击");
    expect(container.textContent).toContain("API Key 已保存");
    expect(container.textContent).not.toContain("Product-level configuration");
    expect(container.textContent).not.toContain("Click through character");
  });
});
