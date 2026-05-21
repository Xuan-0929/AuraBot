import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestOrchestrator } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("ChatOrchestrator reminder tasks", () => {
  it("creates a queued time reminder task instead of a result task", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-reminder-task-");

    const result = await orchestrator.sendMessage({
      content: "两分钟后提醒我喝水"
    });

    expect(result.createdTask).toMatchObject({
      kind: "time_reminder",
      title: "喝水",
      dueAt: expect.any(String),
      status: "queued"
    });
    expect(result.gateDecision).toMatchObject({
      inputKind: "task",
      taskKind: "time_reminder"
    });
    expect(result.assistantMessage?.content).toContain("喝水");
    expect(result.assistantMessage?.content).not.toContain("「");
    expect(result.assistantMessage?.content).not.toContain("」");
    expect(services.taskService.listTasksForProfile(result.userMessage.profileId)).toContainEqual(
      expect.objectContaining({
        kind: "time_reminder",
        title: "喝水"
      })
    );
  });
});
