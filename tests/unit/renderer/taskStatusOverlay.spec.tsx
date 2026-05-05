// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
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

  it("offers cancel for active tasks and delete for settled tasks", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const onCancelTask = vi.fn();
    const onDeleteTask = vi.fn();
    const queuedTask = makeTask({
      id: "task-queued",
      status: "queued",
      title: "帮我写一份 GitHub 周报。"
    });
    const runningTask = makeTask({
      id: "task-running",
      status: "running",
      title: "帮我整理读书笔记。"
    });
    const completedTask = makeTask({
      id: "task-completed",
      status: "completed",
      title: "帮我做一份实践论读书笔记。"
    });

    await act(async () => {
      root.render(
        <TaskStatusOverlay
          tasks={[queuedTask, runningTask, completedTask]}
          onCancelTask={onCancelTask}
          onDeleteTask={onDeleteTask}
        />
      );
    });

    const cancelButtons = Array.from(container.querySelectorAll("button")).filter(
      (button) => button.textContent === "取消"
    );
    const deleteButtons = Array.from(container.querySelectorAll("button")).filter(
      (button) => button.textContent === "删除"
    );

    expect(cancelButtons).toHaveLength(2);
    expect(deleteButtons).toHaveLength(1);

    await act(async () => {
      cancelButtons[0]?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      deleteButtons[0]?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onCancelTask).toHaveBeenCalledWith("task-queued");
    expect(onDeleteTask).toHaveBeenCalledWith("task-completed");

    await act(async () => {
      root.unmount();
    });
  });
});

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    profileId: "profile-1",
    parentSessionId: "session-1",
    sessionId: "task-session-1",
    title: "帮我写一份 GitHub 周报。",
    status: "queued",
    latestProgress: "Task queued from main chat.",
    createdAt: "2026-05-05T00:00:00.000Z",
    updatedAt: "2026-05-05T00:00:02.000Z",
    ...overrides
  };
}
