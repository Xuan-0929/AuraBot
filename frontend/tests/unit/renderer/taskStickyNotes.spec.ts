import { describe, expect, it } from "vitest";
import {
  formatTaskDuration,
  pickConsoleStickyTasks,
  pickVisiblePetTasks,
  summarizeTaskTitle
} from "../../../src/features/tasks/taskStickyNotes";
import type { Task } from "@shared/schemas/task";

describe("task sticky helpers", () => {
  it("keeps pet notes compact and sorts console notes by active then settled", () => {
    const tasks = [
      makeTask({ id: "completed", title: "帮我写读书笔记。", status: "completed", updatedAt: "2026-05-07T08:00:00.000Z" }),
      makeTask({ id: "running", title: "帮我整理周报。", status: "running", updatedAt: "2026-05-07T09:00:00.000Z" }),
      makeTask({ id: "queued", title: "请 帮我生成一下海报！", status: "queued", updatedAt: "2026-05-07T10:00:00.000Z" })
    ];

    expect(pickVisiblePetTasks(tasks).map((task) => task.id)).toEqual(["running", "queued"]);
    expect(pickConsoleStickyTasks(tasks).map((task) => task.id)).toEqual(["queued", "running", "completed"]);
    expect(summarizeTaskTitle(tasks[2].title)).toBe("海报");
  });

  it("formats task duration for the details panel", () => {
    expect(formatTaskDuration(undefined)).toBe("—");
    expect(formatTaskDuration(950)).toBe("950ms");
    expect(formatTaskDuration(61_000)).toBe("1分1秒");
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
