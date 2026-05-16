import { describe, expect, it } from "vitest";
import {
  canTransitionTaskStatus,
  isActiveTask,
  isCancelableTask,
  isDeletableTask,
  isRunnableTask,
  isSettledTask
} from "../../../../src/core/tasks/taskState";
import type { TaskStatus } from "../../../../src/shared/schemas/task";

describe("task state helpers", () => {
  it("classifies task statuses consistently", () => {
    expect(["queued", "running", "waiting"].filter((status) => isActiveTask(status as TaskStatus))).toEqual([
      "queued",
      "running",
      "waiting"
    ]);
    expect(["completed", "failed", "interrupted"].filter((status) => isSettledTask(status as TaskStatus))).toEqual([
      "completed",
      "failed",
      "interrupted"
    ]);
    expect(isRunnableTask("queued")).toBe(true);
    expect(isRunnableTask("running")).toBe(false);
    expect(isCancelableTask("waiting")).toBe(true);
    expect(isDeletableTask("failed")).toBe(true);
    expect(isDeletableTask("queued")).toBe(false);
  });

  it("allows only explicit lifecycle transitions", () => {
    expect(canTransitionTaskStatus("queued", "running")).toBe(true);
    expect(canTransitionTaskStatus("running", "completed")).toBe(true);
    expect(canTransitionTaskStatus("waiting", "interrupted")).toBe(true);
    expect(canTransitionTaskStatus("failed", "queued")).toBe(true);
    expect(canTransitionTaskStatus("completed", "queued")).toBe(false);
    expect(canTransitionTaskStatus("queued", "completed")).toBe(false);
  });
});
