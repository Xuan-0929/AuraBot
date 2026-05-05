// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { TaskStatusOverlay } from "../../../src/renderer/src/features/tasks/TaskStatusOverlay";
import type { Task } from "../../../src/shared/schemas/task";

describe("TaskStatusOverlay", () => {
  it("renders task status in human language without leaking runner internals", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const task: Task = {
      id: "task-weekly",
      profileId: "profile-1",
      parentSessionId: "session-1",
      sessionId: "task-session-1",
      title: "帮我写一份 GitHub 周报。",
      status: "completed",
      latestProgress: "Codex 子任务完成：\nGitHub 周报写好了，我整理成文件放好了。",
      createdAt: "2026-05-05T00:00:00.000Z",
      updatedAt: "2026-05-05T00:00:02.000Z"
    };

    await act(async () => {
      root.render(<TaskStatusOverlay tasks={[task]} />);
    });

    expect(container.textContent).toContain("完成");
    expect(container.textContent).toContain("GitHub 周报");
    expect(container.textContent).toContain("结果已经准备好");
    expect(container.textContent).not.toContain("帮我写一份");
    expect(container.textContent).not.toContain("Codex");
    expect(container.textContent).not.toContain("completed");

    await act(async () => {
      root.unmount();
    });
  });
});
