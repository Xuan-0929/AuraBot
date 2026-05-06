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
      provider: "local-stub",
      model: "gate-stub",
      enabled: false,
      hasApiKey: false
    },
    memoryModel: {
      provider: "openai-compatible",
      model: "memory-model",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
      hasApiKey: true
    }
  },
  onUpdatePetSettings: vi.fn(async () => undefined),
  onOpenConfigFile: vi.fn(async () => undefined)
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
    expect(container.textContent).toContain("Character");
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
    expect(container.textContent).toContain("打开 config 文件");
    expect(container.textContent).toContain("人物");
    expect(container.textContent).toContain("穿透人物点击");
    expect(container.textContent).toContain("API Key 已保存");
    expect(container.textContent).not.toContain("Product-level configuration");
    expect(container.textContent).not.toContain("Click through character");
  });
});
