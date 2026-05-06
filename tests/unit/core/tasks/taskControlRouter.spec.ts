import { describe, expect, it } from "vitest";
import type { Task } from "../../../../src/shared/schemas/task";
import { parseTaskControlRequest, selectTaskForControl } from "../../../../src/core/tasks/taskControlRouter";

const baseTask = {
  profileId: "profile",
  parentSessionId: "main",
  sessionId: "task-session",
  kind: "result_output",
  status: "queued",
  latestProgress: "",
  createdAt: "2026-05-04T00:00:00.000Z",
  updatedAt: "2026-05-04T00:00:00.000Z"
} satisfies Omit<Task, "id" | "title">;

describe("parseTaskControlRequest", () => {
  it("extracts a target hint and clean revised title from a main-chat revision", () => {
    expect(parseTaskControlRequest("把会议任务改成整理后天路演的清单。")).toEqual({
      action: "revise",
      targetHint: "会议",
      revisedTitle: "整理后天路演的清单。"
    });
  });

  it("extracts a target hint from an interrupt command", () => {
    expect(parseTaskControlRequest("停掉周报任务。")).toEqual({
      action: "interrupt",
      targetHint: "周报"
    });
  });
});

describe("selectTaskForControl", () => {
  it("selects the matching task by hint instead of falling back to the newest task", () => {
    const meetingTask: Task = {
      ...baseTask,
      id: "meeting",
      title: "帮我整理一份明天会议的清单。"
    };
    const weeklyTask: Task = {
      ...baseTask,
      id: "weekly",
      title: "帮我生成一份周报初稿。"
    };

    expect(selectTaskForControl({ tasks: [weeklyTask, meetingTask], targetHint: "会议" })?.id).toBe("meeting");
    expect(selectTaskForControl({ tasks: [weeklyTask, meetingTask], targetHint: "周报", activeOnly: true })?.id).toBe(
      "weekly"
    );
  });
});
