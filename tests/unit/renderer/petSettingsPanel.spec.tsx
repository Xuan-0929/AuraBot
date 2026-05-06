// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PetSettingsPanel, type PetSettingsPanelProps } from "../../../src/renderer/src/features/settings/PetSettingsPanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps: PetSettingsPanelProps = {
  settings: {
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
          command: "/usr/local/bin/codex"
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
      pollIntervalMs: 10000,
      followUpLookaheadMs: 0,
      idleThresholdMs: 600000,
      idleCooldownMs: 1800000
    }
  },
  onUpdate: vi.fn(async () => undefined),
  onOpenSettings: vi.fn(async () => undefined)
};

describe("PetSettingsPanel settings menu", () => {
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

  it("opens a lightweight settings submenu with quick controls and a full settings entry", async () => {
    const onUpdate = vi.fn(async () => undefined);
    const onOpenSettings = vi.fn(async () => undefined);

    await renderPanel({ onUpdate, onOpenSettings });

    expect(container.querySelector('[role="menu"]')).toBeNull();

    await act(async () => {
      getButton("Open settings menu").click();
    });

    expect(getButton("Open settings menu").getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector('[role="menu"]')?.textContent).toContain("Quick settings");
    expect(container.querySelector('[role="menu"]')?.textContent).not.toContain("Model config");
    expect(container.querySelector<HTMLSelectElement>('[aria-label="Language"]')?.value).toBe("en");
    expect(container.querySelector<HTMLInputElement>('[aria-label="Click through character"]')?.checked).toBe(false);

    await act(async () => {
      getButton("Open full settings").click();
    });

    expect(onOpenSettings).toHaveBeenCalledTimes(1);

    await act(async () => {
      const language = container.querySelector<HTMLSelectElement>('[aria-label="Language"]')!;
      language.value = "zh";
      language.dispatchEvent(new Event("change", { bubbles: true }));
    });

    await act(async () => {
      const clickThrough = container.querySelector<HTMLInputElement>('[aria-label="Click through character"]')!;
      clickThrough.click();
    });

    expect(onUpdate).toHaveBeenCalledWith({ displayLanguage: "zh" });
    expect(onUpdate).toHaveBeenCalledWith({ clickThroughPet: true });
  });

  it("localizes the console shortcut menu in Chinese", async () => {
    await renderPanel({
      settings: {
        clickThroughPet: false,
        displayLanguage: "zh"
      }
    });

    await act(async () => {
      getButton("打开设置菜单").click();
    });

    expect(container.querySelector('[role="menu"]')?.textContent).toContain("快捷设置");
    expect(container.querySelector('[role="menu"]')?.textContent).toContain("打开完整设置");
    expect(container.querySelector('[role="menu"]')?.textContent).toContain("穿透人物点击");
    expect(container.querySelector('[role="menu"]')?.textContent).not.toContain("Quick settings");
    expect(container.querySelector('[role="menu"]')?.textContent).not.toContain("Click through character");
  });

  async function renderPanel(overrides: Partial<PetSettingsPanelProps> = {}) {
    await act(async () => {
      root.render(<PetSettingsPanel {...baseProps} {...overrides} />);
    });
  }

  function getButton(name: string): HTMLButtonElement {
    const button = Array.from(container.querySelectorAll("button")).find(
      (candidate) => candidate.getAttribute("aria-label") === name
    );

    expect(button).toBeTruthy();

    return button as HTMLButtonElement;
  }
});
