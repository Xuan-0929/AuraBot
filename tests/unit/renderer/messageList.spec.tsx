// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MessageList } from "../../../src/renderer/src/features/chat/MessageList";
import type { Message } from "../../../src/shared/schemas/message";

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
});
