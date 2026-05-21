import { afterEach, describe, expect, it } from "vitest";
import { TaskRepository } from "../../../packages/core-legacy/src/storage/repositories/taskRepository";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("TaskRepository", () => {
  it("transitions a task only when the current status matches", () => {
    const services = createTestServices("aurabot-task-repository-");
    const repository = new TaskRepository(services.database);
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });

    const running = repository.transitionStatus({
      taskId: task.id,
      from: "queued",
      to: "running",
      latestProgress: "开始处理：GitHub 周报"
    });
    const duplicateStart = repository.transitionStatus({
      taskId: task.id,
      from: "queued",
      to: "running",
      latestProgress: "重复开始"
    });
    const missing = repository.transitionStatus({
      taskId: "missing-task",
      from: "queued",
      to: "running",
      latestProgress: "开始处理"
    });

    expect(running?.status).toBe("running");
    expect(duplicateStart).toBeUndefined();
    expect(missing).toBeUndefined();
    expect(repository.getById(task.id)?.latestProgress).toBe("开始处理：GitHub 周报");
  });

  it("rejects transitions that are not allowed by the task state machine", () => {
    const services = createTestServices("aurabot-task-repository-invalid-transition-");
    const repository = new TaskRepository(services.database);
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "生成一份任务系统优化清单。"
    });

    const completed = repository.transitionStatus({
      taskId: task.id,
      from: "queued",
      to: "completed",
      latestProgress: "直接完成"
    });

    expect(completed).toBeUndefined();
    expect(repository.getById(task.id)?.status).toBe("queued");
  });
});
