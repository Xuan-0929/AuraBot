import { afterEach, describe, expect, it } from "vitest";
import { TaskArtifactRepository } from "../../../src/core/storage/repositories/taskArtifactRepository";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("TaskArtifactRepository", () => {
  it("creates and lists file artifacts for a task", () => {
    const services = createTestServices("aurabot-task-artifact-");
    const repository = new TaskArtifactRepository(services.database);
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });

    const artifact = repository.create({
      taskId: task.id,
      artifactType: "file",
      uri: "/Users/xuan/project/report.md"
    });

    expect(artifact).toMatchObject({
      taskId: task.id,
      artifactType: "file",
      uri: "/Users/xuan/project/report.md"
    });
    expect(repository.listByTask(task.id)).toEqual([artifact]);
    expect(repository.isUriAllowed(task.id, "/Users/xuan/project/report.md")).toBe(true);
    expect(repository.isUriAllowed(task.id, "/Users/xuan/project/other.md")).toBe(false);
  });
});
