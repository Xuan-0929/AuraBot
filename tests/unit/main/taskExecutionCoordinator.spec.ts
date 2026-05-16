import { afterEach, describe, expect, it, vi } from "vitest";
import { TaskExecutionCoordinator } from "../../../src/main/taskExecutionCoordinator";
import type { AgentStatus } from "../../../src/shared/contracts/chat";
import type { PetActivity } from "../../../src/shared/contracts/pet";
import type { Task } from "../../../src/shared/schemas/task";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("TaskExecutionCoordinator", () => {
  it("broadcasts progress, creates a settled message, and records file artifacts", async () => {
    const services = createTestServices("aurabot-task-coordinator-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });
    const changedTasks: Task[] = [];
    const statuses: AgentStatus[] = [];
    const activities: PetActivity[] = [];
    const coordinator = new TaskExecutionCoordinator(services, {
      broadcastTaskChanged: (changedTask) => changedTasks.push(changedTask),
      broadcastAgentStatus: (status) => statuses.push(status),
      broadcastChatMessage: vi.fn(),
      broadcastPetActivity: (activity) => activities.push(activity)
    });

    services.taskRepository.appendEvent({
      taskId: task.id,
      eventType: "task.agent.progress",
      summary: "Codex 子任务新增文件：/Users/xuan/project/docs/report.md",
      payload: {
        fileChange: {
          kind: "add",
          path: "/Users/xuan/project/docs/report.md"
        }
      }
    });

    const started = coordinator.startTaskInBackground({
      profileId: profile.id,
      taskId: task.id
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const assistantMessages = services.messageRepository
      .listBySession(mainSession.id)
      .filter((message) => message.role === "assistant");
    const artifacts = services.taskArtifactRepository.listByTask(task.id);

    expect(started?.status).toBe("running");
    expect(changedTasks.map((changedTask) => changedTask.status)).toContain("running");
    expect(changedTasks.map((changedTask) => changedTask.status)).toContain("completed");
    expect(statuses).toContain("idle");
    expect(assistantMessages.at(-1)?.attachments?.[0]?.path).toBe("/Users/xuan/project/docs/report.md");
    expect(artifacts).toEqual([
      expect.objectContaining({
        taskId: task.id,
        artifactType: "file",
        uri: "/Users/xuan/project/docs/report.md"
      })
    ]);
    expect(activities.at(-1)?.kind).toBe("task_completed");
  });
});
