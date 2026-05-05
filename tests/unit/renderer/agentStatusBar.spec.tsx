// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { AgentStatusBar } from "../../../src/renderer/src/features/status/AgentStatusBar";

describe("AgentStatusBar", () => {
  it("renders Chinese status labels when the display language is Chinese", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);

    await act(async () => {
      root.render(<AgentStatusBar status="typing" displayLanguage="zh" />);
    });

    expect(container.textContent).toContain("对方正在输入中");

    await act(async () => {
      root.render(<AgentStatusBar status="listening" displayLanguage="zh" />);
    });

    expect(container.textContent).toContain("倾听中");

    await act(async () => {
      root.render(<AgentStatusBar status="tasking" displayLanguage="zh" />);
    });

    expect(container.textContent).toContain("任务中");

    await act(async () => {
      root.unmount();
    });
  });

  it("renders English status labels by default", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);

    await act(async () => {
      root.render(<AgentStatusBar status="typing" displayLanguage="en" />);
    });

    expect(container.textContent).toContain("typing");

    await act(async () => {
      root.unmount();
    });
  });
});
