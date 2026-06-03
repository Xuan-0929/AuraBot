import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { TaskInputArtifactRepository } from "@core/storage/repositories/taskInputArtifactRepository";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("TaskInputArtifactRepository", () => {
  it("records input files separately from output artifacts and validates staged paths", () => {
    const services = createTestServices("aurabot-task-input-artifact-");
    const repository = new TaskInputArtifactRepository(services.database);
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "分析上传的图片"
    });
    const stagedPath = join(services.config.storagePath, "..", "task-inputs", "image.png");
    mkdirSync(join(services.config.storagePath, "..", "task-inputs"), { recursive: true });
    writeFileSync(stagedPath, "fake image", "utf8");

    const artifact = repository.create({
      taskId: task.id,
      artifactType: "image",
      originalName: "image.png",
      mimeType: "image/png",
      sizeBytes: 10,
      uri: stagedPath
    });

    expect(repository.listByTask(task.id)).toEqual([artifact]);
    expect(repository.isUriAllowed(task.id, stagedPath)).toBe(true);
    expect(repository.isUriAllowed(task.id, "/tmp/not-this-file.png")).toBe(false);
    expect(services.taskArtifactRepository.listByTask(task.id)).toEqual([]);
  });
});
