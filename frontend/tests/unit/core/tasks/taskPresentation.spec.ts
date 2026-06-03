import { describe, expect, it } from "vitest";
import type { Task, TaskEvent } from "@shared/schemas/task";
import {
  buildTaskSettledMessageDraft,
  buildTaskSettledMessageDraftWithVoice,
  buildTaskStatusResponse,
  buildTaskStatusResponseWithVoice,
  isTaskResultPathAllowed
} from "@core/tasks/taskPresentation";
import { DeterministicVoiceAdapter } from "@core/voice/voiceAdapter";

const baseTask: Task = {
  id: "task-weekly",
  profileId: "profile-1",
  parentSessionId: "session-main",
  sessionId: "session-task",
  kind: "result_output",
  title: "帮我做一份 GitHub 周报。",
  status: "completed",
  latestProgress: "Codex 子任务完成：\nGitHub 周报写好了，文件在 /Users/xuan/project/docs/report.md",
  createdAt: "2026-05-05T00:00:00.000Z",
  updatedAt: "2026-05-05T00:00:10.000Z"
};

function taskEvent(input: Partial<TaskEvent> & Pick<TaskEvent, "eventType">): TaskEvent {
  return {
    id: `event-${input.eventType}`,
    taskId: baseTask.id,
    eventType: input.eventType,
    summary: input.summary ?? input.eventType,
    payload: input.payload ?? {},
    createdAt: input.createdAt ?? "2026-05-05T00:00:05.000Z"
  };
}

describe("task presentation", () => {
  it("turns a completed child task with a file result into a warm message and file attachment", () => {
    const events = [
      taskEvent({
        eventType: "task.agent.progress",
        summary: "Codex 子任务新增文件：/Users/xuan/project/docs/report.md",
        payload: {
          fileChange: {
            kind: "add",
            path: "/Users/xuan/project/docs/report.md"
          }
        }
      }),
      taskEvent({
        eventType: "task.completed",
        summary: "Codex 子任务完成：\nGitHub 周报写好了，文件在 /Users/xuan/project/docs/report.md",
        payload: {
          outputFile: "/Users/xuan/Library/Application Support/AuraBot/task-agent-output/task-weekly-last-message.md"
        }
      })
    ];

    const draft = buildTaskSettledMessageDraft(baseTask, events);

    expect(draft.content).toBe("好啦，GitHub 周报写好了。文件我放在下面，你可以直接打开看，也可以去文件夹里找。");
    expect(draft.content).not.toContain("task-weekly");
    expect(draft.content).not.toContain("Codex");
    expect(draft.content).not.toContain("/Users/xuan/project/docs/report.md");
    expect(draft.attachments).toEqual([
      {
        id: "task-weekly-file-0",
        kind: "file",
        taskId: "task-weekly",
        name: "report.md",
        path: "/Users/xuan/project/docs/report.md"
      }
    ]);
    expect(isTaskResultPathAllowed(events, "/Users/xuan/project/docs/report.md")).toBe(true);
    expect(isTaskResultPathAllowed(events, "/Users/xuan/project/docs/other.md")).toBe(false);
  });

  it("uses a conversational result when a completed task has no file attachment", () => {
    const draft = buildTaskSettledMessageDraft(
      {
        ...baseTask,
        title: "帮我写一份 GitHub 周报。",
        latestProgress: "已完成：GitHub 周报"
      },
      [
        taskEvent({
          eventType: "task.completed",
          summary: "已完成：GitHub 周报"
        })
      ]
    );

    expect(draft.content).toBe("好啦，GitHub 周报写好了。");
    expect(draft.attachments).toEqual([]);
  });

  it("prefers structured task artifacts over event payload paths", () => {
    const draft = buildTaskSettledMessageDraft(
      baseTask,
      [
        taskEvent({
          eventType: "task.agent.progress",
          summary: "Codex 子任务新增文件：/Users/xuan/project/docs/event-report.md",
          payload: {
            fileChange: {
              kind: "add",
              path: "/Users/xuan/project/docs/event-report.md"
            }
          }
        })
      ],
      [
        {
          id: "artifact-1",
          taskId: baseTask.id,
          artifactType: "file",
          uri: "/Users/xuan/project/docs/artifact-report.md",
          createdAt: "2026-05-05T00:00:06.000Z"
        }
      ]
    );

    expect(draft.attachments).toEqual([
      {
        id: "task-weekly-file-0",
        kind: "file",
        taskId: "task-weekly",
        name: "artifact-report.md",
        path: "/Users/xuan/project/docs/artifact-report.md"
      }
    ]);
  });

  it("uses a companion-first queued reply instead of a runner-style status", () => {
    const response = buildTaskStatusResponse({
      ...baseTask,
      status: "queued",
      title: "帮我做一个 GitHub 周报。",
      latestProgress: "Task queued from main chat."
    });

    expect(response).toBe("「GitHub 周报」这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。");
    expect(response).not.toContain("帮我");
    expect(response).not.toContain("做一个");
    expect(response).not.toContain("queued");
  });

  it("keeps running task updates in Aura's voice instead of task-runner wording", () => {
    const response = buildTaskStatusResponse(
      {
        ...baseTask,
        status: "running",
        title: "帮我做一个 GitHub 周报。",
        latestProgress: "正在整理本周合并记录"
      },
      [
        taskEvent({
          eventType: "task.agent.progress",
          summary: "正在整理本周合并记录"
        })
      ]
    );

    expect(response).toBe("「GitHub 周报」这件事我已经在弄了，最近一步是：正在整理本周合并记录。\n弄好后我会回来跟你说。");
    expect(response).not.toContain("正在处理");
    expect(response).not.toContain("runner");
  });

  it("keeps failed task messages gentle while preserving the key failure detail", () => {
    const draft = buildTaskSettledMessageDraft(
      {
        ...baseTask,
        status: "failed",
        latestProgress: "Codex 子任务失败：codex exited 1"
      },
      [
        taskEvent({
          eventType: "task.failed",
          summary: "Codex 子任务失败：codex exited 1"
        })
      ]
    );

    expect(draft.content).toBe("这次我先卡在 codex exited 1 这一步了。你要是愿意，我们接着一起改。");
    expect(draft.content).not.toContain("failed");
    expect(draft.attachments).toEqual([]);
  });

  it("keeps queued task status companion-first through the voice adapter path", async () => {
    const response = await buildTaskStatusResponseWithVoice(
      {
        ...baseTask,
        status: "queued",
        title: "帮我做一个 GitHub 周报。",
        latestProgress: "Task queued from main chat."
      },
      [],
      {
        voiceAdapter: new DeterministicVoiceAdapter()
      }
    );

    expect(response).toBe("「GitHub 周报」这件事我先替你记在旁边了。你想让我现在开始的话，直接跟我说一声就行。");
    expect(response).not.toContain("queued");
    expect(response).not.toContain("还没开始");
  });

  it("keeps failed task wording aligned through the voice adapter path", async () => {
    const draft = await buildTaskSettledMessageDraftWithVoice(
      {
        ...baseTask,
        status: "failed",
        latestProgress: "Codex 子任务失败：codex exited 1"
      },
      [
        taskEvent({
          eventType: "task.failed",
          summary: "Codex 子任务失败：codex exited 1"
        })
      ],
      {
        voiceAdapter: new DeterministicVoiceAdapter()
      }
    );

    expect(draft.content).toBe("这次我先卡在 codex exited 1 这一步了。你要是愿意，我们接着一起改。");
    expect(draft.content).not.toContain("没跑通");
  });

  it("routes settled task text through the voice adapter while preserving attachments", async () => {
    const events = [
      taskEvent({
        eventType: "task.agent.progress",
        summary: "Codex 子任务新增文件：/Users/xuan/project/docs/report.md",
        payload: {
          fileChange: {
            kind: "add",
            path: "/Users/xuan/project/docs/report.md"
          }
        }
      }),
      taskEvent({
        eventType: "task.completed",
        summary: "Codex 子任务完成：\nGitHub 周报写好了，文件在 /Users/xuan/project/docs/report.md"
      })
    ];

    const draft = await buildTaskSettledMessageDraftWithVoice(baseTask, events, {
      voiceAdapter: new DeterministicVoiceAdapter()
    });

    expect(draft.content).toBe("好啦，GitHub 周报写好了。文件我放在下面，你先看一眼，还想改的话我接着调。");
    expect(draft.content).not.toContain("任务完成：");
    expect(draft.content).not.toContain("Codex");
    expect(draft.attachments).toHaveLength(1);
  });
});
