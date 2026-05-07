// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageList } from "../../../src/renderer/src/features/chat/MessageList";
import type { Message } from "../../../src/shared/schemas/message";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("MessageList task result attachments", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders file action buttons without showing the raw file path", async () => {
    const openTaskResultFile = vi.fn();
    const showTaskResultFileInFolder = vi.fn();
    vi.stubGlobal("window", {
      aurabot: {
        openTaskResultFile,
        showTaskResultFileInFolder
      }
    });
    const container = document.createElement("div");
    const root = createRoot(container);
    const message: Message = {
      id: "message-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "好啦，GitHub 周报写好了。文件我放在下面，你可以直接打开看，也可以去文件夹里找。",
      attachments: [
        {
          id: "task-weekly-file-0",
          kind: "file",
          taskId: "task-weekly",
          name: "report.md",
          path: "/Users/xuan/project/docs/report.md"
        }
      ],
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    await act(async () => {
      root.render(<MessageList messages={[message]} />);
    });

    expect(container.textContent).toContain("report.md");
    expect(container.textContent).not.toContain("/Users/xuan/project/docs/report.md");

    const [openButton, revealButton] = Array.from(container.querySelectorAll("button"));
    openButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    revealButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(openTaskResultFile).toHaveBeenCalledWith({
      taskId: "task-weekly",
      path: "/Users/xuan/project/docs/report.md"
    });
    expect(showTaskResultFileInFolder).toHaveBeenCalledWith({
      taskId: "task-weekly",
      path: "/Users/xuan/project/docs/report.md"
    });

    await act(async () => {
      root.unmount();
    });
  });

  it("renders assistant replies as sentence-level chat bubbles", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const message: Message = {
      id: "message-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "assistant",
      content: "我在，刚才那件事我已经记住了。你继续说！第三句还在写",
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    await act(async () => {
      root.render(<MessageList messages={[message]} />);
    });

    const assistantBubbles = Array.from(container.querySelectorAll(".message-assistant"));
    expect(assistantBubbles).toHaveLength(3);
    expect(assistantBubbles.map((bubble) => bubble.textContent)).toEqual([
      "Aura我在，刚才那件事我已经记住了。",
      "你继续说！",
      "第三句还在写"
    ]);

    await act(async () => {
      root.unmount();
    });
  });

  it("keeps user messages as one bubble even when they contain several sentences", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const message: Message = {
      id: "message-1",
      sessionId: "session-1",
      profileId: "profile-1",
      role: "user",
      content: "第一句。第二句。",
      createdAt: "2026-05-05T00:00:00.000Z"
    };

    await act(async () => {
      root.render(<MessageList messages={[message]} />);
    });

    const userBubbles = Array.from(container.querySelectorAll(".message-user"));
    expect(userBubbles).toHaveLength(1);
    expect(userBubbles[0]?.textContent).toBe("You第一句。第二句。");

    await act(async () => {
      root.unmount();
    });
  });

  it("uses a quieter empty state for the chat room shell", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);

    await act(async () => {
      root.render(<MessageList messages={[]} displayLanguage="zh" />);
    });

    expect(container.textContent).toContain("主对话");
    expect(container.textContent).toContain("我在，今天想先从哪件事开始？");
    expect(container.querySelector("h1")).toBeNull();

    await act(async () => {
      root.unmount();
    });
  });
});
