import { describe, expect, it } from "vitest";
import { buildTaskDetail } from "../../../../packages/core-legacy/src/tasks/taskDetails";
import type { Task } from "../../../../packages/shared/src/schemas/task";

describe("buildTaskDetail", () => {
  it("summarizes execution context, timeline, artifacts, and errors without exposing raw stdio", () => {
    const detail = buildTaskDetail(
      makeTask({ status: "failed", latestProgress: "Codex 子任务失败：权限不足" }),
      [
        {
          id: "event-started",
          taskId: "task-1",
          eventType: "task.agent.started",
          summary: "Codex 子任务已启动",
          payload: {
            runnerId: "codex",
            cwd: "/tmp/project",
            sandbox: "workspace-write",
            startedAt: "2026-05-16T10:00:00.000Z",
            stdout: "不应该展示完整 stdout"
          },
          createdAt: "2026-05-16T10:00:00.000Z"
        },
        {
          id: "event-failed",
          taskId: "task-1",
          eventType: "task.failed",
          summary: "Codex 子任务失败：权限不足",
          payload: {
            runnerId: "codex",
            completedAt: "2026-05-16T10:00:03.250Z",
            durationMs: 3250,
            stderr: "一大段错误堆栈"
          },
          createdAt: "2026-05-16T10:00:03.250Z"
        }
      ],
      [
        {
          id: "artifact-1",
          taskId: "task-1",
          artifactType: "file",
          uri: "/tmp/project/report.md",
          createdAt: "2026-05-16T10:00:03.000Z"
        }
      ]
    );

    expect(detail.executor).toEqual({
      runnerId: "codex",
      cwd: "/tmp/project",
      sandbox: "workspace-write"
    });
    expect(detail.startedAt).toBe("2026-05-16T10:00:00.000Z");
    expect(detail.completedAt).toBe("2026-05-16T10:00:03.250Z");
    expect(detail.durationMs).toBe(3250);
    expect(detail.recentError).toBe("Codex 子任务失败：权限不足");
    expect(detail.artifacts).toMatchObject([{ name: "report.md", uri: "/tmp/project/report.md" }]);
    expect(detail.timeline.at(1)?.payloadSummary).toEqual({
      runnerId: "codex",
      completedAt: "2026-05-16T10:00:03.250Z",
      durationMs: 3250,
      stderrBytes: 21
    });
    expect(JSON.stringify(detail)).not.toContain("一大段错误堆栈");
    expect(JSON.stringify(detail)).not.toContain("不应该展示完整 stdout");
  });
});

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "task-1",
    profileId: "profile-1",
    parentSessionId: "session-1",
    sessionId: "task-session-1",
    kind: "result_output",
    title: "整理任务系统改进方案",
    status: "running",
    latestProgress: "处理中",
    createdAt: "2026-05-16T09:59:59.000Z",
    updatedAt: "2026-05-16T10:00:03.250Z",
    ...overrides
  };
}
