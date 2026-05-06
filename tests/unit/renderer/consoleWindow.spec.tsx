// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleWindow, type ConsoleWindowProps } from "../../../src/renderer/src/features/console/ConsoleWindow";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseProps: ConsoleWindowProps = {
  agentStatus: "idle",
  messages: [],
  tasks: [],
  petSettings: {
    clickThroughPet: false,
    displayLanguage: "zh"
  },
  appConfig: undefined,
  onUpdatePetSettings: vi.fn(async () => undefined),
  onOpenSettings: vi.fn(async () => undefined),
  onLoadMemoryDebugSnapshot: vi.fn(async () => undefined),
  onSend: vi.fn(async () => undefined),
  onCancelTask: vi.fn(async () => undefined),
  onDeleteTask: vi.fn(async () => undefined)
};

describe("ConsoleWindow localization", () => {
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

  it("uses Chinese text across the console shell when Chinese is selected", async () => {
    await act(async () => {
      root.render(<ConsoleWindow {...baseProps} />);
    });

    expect(container.textContent).toContain("AuraBot 控制台");
    expect(container.textContent).toContain("桌宠需要完整对话线程时，在这里继续聊。");
    expect(container.textContent).toContain("我在，随时可以开始。");
    expect(container.textContent).toContain("设置");
    expect(container.querySelector("textarea")?.getAttribute("placeholder")).toBe("说点什么");
    expect(container.textContent).not.toContain("Chat here when");
    expect(container.textContent).not.toContain("Ready when you are");
    expect(container.textContent).not.toContain("Say something");
  });
});
