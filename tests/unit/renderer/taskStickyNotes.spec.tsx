// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { TaskStickyNotes } from "../../../src/renderer/src/features/tasks/TaskStickyNotes";
import type { Task } from "../../../src/shared/schemas/task";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("TaskStickyNotes", () => {
  let container: HTMLDivElement;
  let root: ReturnType<typeof createRoot>;

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
  });

  it("shows at most two sticky notes in pet mode and uses the existing task title cleanup", async () => {
    const tasks = [
      makeTask({ id: "task-1", title: "帮我写周报。", status: "running" }),
      makeTask({ id: "task-2", title: "晚上提醒我回消息。", kind: "time_reminder", status: "queued" }),
      makeTask({ id: "task-3", title: "帮我整理文档。", status: "queued" })
    ];

    await act(async () => {
      root.render(<TaskStickyNotes tasks={tasks} variant="pet" displayLanguage="zh" />);
    });

    const notes = container.querySelector<HTMLElement>('[aria-label="今日便签"]');
    expect(notes).not.toBeNull();
    expect(notes?.textContent).toContain("周报");
    expect(notes?.textContent).toContain("晚上提醒我回消息");
    expect(notes?.textContent).not.toContain("整理文档");
  });

  it("does not render console action buttons in task 2 even when action handlers are provided", async () => {
    const tasks = [
      makeTask({ id: "task-1", title: "帮我写周报。", status: "running" }),
      makeTask({ id: "task-2", title: "晚上提醒我回消息。", kind: "time_reminder", status: "completed" })
    ];

    await act(async () => {
      root.render(
        <TaskStickyNotes
          tasks={tasks}
          variant="console"
          displayLanguage="zh"
          onCancelTask={() => undefined}
          onDeleteTask={() => undefined}
        />
      );
    });

    expect(container.textContent).toContain("周报");
    expect(container.querySelector(".task-sticky-note-actions")).toBeNull();
    expect(container.querySelector("button")).toBeNull();
  });

  it("renders the full console task list without choosing a console subset internally", async () => {
    const tasks = [
      makeTask({ id: "task-1", title: "帮我写周报。", status: "running" }),
      makeTask({ id: "task-2", title: "晚上提醒我回消息。", kind: "time_reminder", status: "queued" }),
      makeTask({ id: "task-3", title: "帮我整理文档。", status: "queued" }),
      makeTask({ id: "task-4", title: "帮我写一份 GitHub 周报。", status: "completed" })
    ];

    await act(async () => {
      root.render(<TaskStickyNotes tasks={tasks} variant="console" displayLanguage="zh" />);
    });

    expect(container.textContent).toContain("周报");
    expect(container.textContent).toContain("晚上提醒我回消息");
    expect(container.textContent).toContain("文档");
    expect(container.textContent).toContain("GitHub 周报");
  });
});

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    profileId: "profile-1",
    parentSessionId: "session-1",
    sessionId: "task-session-1",
    kind: "result_output",
    title: "帮我写一份 GitHub 周报。",
    status: "queued",
    latestProgress: "Task queued from main chat.",
    createdAt: "2026-05-05T00:00:00.000Z",
    updatedAt: "2026-05-05T00:00:02.000Z",
    ...overrides
  };
}
