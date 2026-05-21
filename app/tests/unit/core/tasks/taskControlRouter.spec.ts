import { describe, expect, it } from "vitest";
import type { Task } from "../../../../packages/shared/src/schemas/task";
import {
  parseTaskControlRequest,
  selectTaskForControl,
  selectTaskForControlWithReason
} from "../../../../packages/core-legacy/src/tasks/taskControlRouter";

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
    expect(parseTaskControlRequest("把喝水提醒改到明天早上九点")).toEqual({
      action: "revise",
      targetHint: "喝水提醒",
      revisedTitle: "明天早上九点"
    });
  });

  it("extracts a target hint from an interrupt command", () => {
    expect(parseTaskControlRequest("停掉周报任务。")).toEqual({
      action: "interrupt",
      targetHint: "周报"
    });
  });

  it("recognizes retry, continue, open result, and delete controls", () => {
    expect(parseTaskControlRequest("重试周报任务")).toEqual({
      action: "retry",
      targetHint: "周报"
    });
    expect(parseTaskControlRequest("继续会议任务")).toEqual({
      action: "continue",
      targetHint: "会议"
    });
    expect(parseTaskControlRequest("打开周报结果")).toEqual({
      action: "open_result",
      targetHint: "周报"
    });
    expect(parseTaskControlRequest("删除已完成的会议任务")).toEqual({
      action: "delete",
      targetHint: "会议"
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

  it("can ask for clarification when candidates are too close", () => {
    const firstTask: Task = {
      ...baseTask,
      id: "weekly-a",
      title: "帮我整理一份产品周报。"
    };
    const secondTask: Task = {
      ...baseTask,
      id: "weekly-b",
      title: "帮我整理一份研发周报。"
    };

    const result = selectTaskForControlWithReason({
      tasks: [firstTask, secondTask],
      targetHint: "周报"
    });

    expect(result.reason).toBe("ambiguous");
    expect(result.candidates.map((task) => task.id)).toEqual(["weekly-a", "weekly-b"]);
  });

  it("uses the control action to prefer tasks in a compatible status", () => {
    const queuedWeekly: Task = {
      ...baseTask,
      id: "queued-weekly",
      title: "帮我整理一份周报。",
      status: "queued"
    };
    const completedWeekly: Task = {
      ...baseTask,
      id: "completed-weekly",
      title: "帮我整理一份周报。",
      status: "completed"
    };
    const failedWeekly: Task = {
      ...baseTask,
      id: "failed-weekly",
      title: "帮我整理一份周报。",
      status: "failed"
    };

    expect(
      selectTaskForControlWithReason({
        tasks: [queuedWeekly, completedWeekly],
        targetHint: "周报",
        action: "delete"
      }).task?.id
    ).toBe("completed-weekly");
    expect(
      selectTaskForControlWithReason({
        tasks: [queuedWeekly, failedWeekly],
        targetHint: "周报",
        action: "retry"
      }).task?.id
    ).toBe("failed-weekly");
  });

  it("uses updatedAt as a tie breaker for equally compatible task matches", () => {
    const olderWeekly: Task = {
      ...baseTask,
      id: "older-weekly",
      title: "帮我整理一份周报。",
      updatedAt: "2026-05-04T00:00:00.000Z"
    };
    const newerWeekly: Task = {
      ...baseTask,
      id: "newer-weekly",
      title: "帮我整理一份周报。",
      updatedAt: "2026-05-05T00:00:00.000Z"
    };

    const result = selectTaskForControlWithReason({
      tasks: [olderWeekly, newerWeekly],
      targetHint: "周报",
      action: "query"
    });

    expect(result.reason).toBe("selected");
    expect(result.task?.id).toBe("newer-weekly");
  });
});
