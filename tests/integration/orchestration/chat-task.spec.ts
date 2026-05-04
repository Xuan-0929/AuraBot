import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestOrchestrator } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("chat task integration", () => {
  it("creates a visible task when gate classifies the message as a task request", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    const result = await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);

    expect(result.gateDecision?.inputKind).toBe("task");
    expect(result.createdTask?.title).toContain("明天会议");
    expect(result.assistantMessage?.content).toContain("任务");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.status).toBe("queued");
    expect(memoryContext.nodes).toHaveLength(0);
  });

  it("lets the main chat query, interrupt, and revise the latest task", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });
    const interrupt = await orchestrator.sendMessage({ content: "先停掉这个任务。" });
    const revise = await orchestrator.sendMessage({ content: "改成整理后天路演的清单。" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(query.gateDecision?.inputKind).toBe("task_control");
    expect(query.assistantMessage?.content).toContain("queued");
    expect(interrupt.updatedTask?.status).toBe("interrupted");
    expect(interrupt.assistantMessage?.content).toContain("停下");
    expect(revise.updatedTask?.status).toBe("queued");
    expect(revise.updatedTask?.title).toContain("后天路演");
    expect(revise.assistantMessage?.content).toContain("后天路演");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.title).toContain("后天路演");
  });

  it("starts the latest queued task from main chat and reports completed progress", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const start = await orchestrator.sendMessage({ content: "开始做这个任务。" });
    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(start.updatedTask?.status).toBe("completed");
    expect(start.assistantMessage?.content).toContain("完成");
    expect(query.assistantMessage?.content).toContain("completed");
    expect(query.assistantMessage?.content).toContain("明天会议");
    expect(tasks[0]?.latestProgress).toContain("完成");
  });
});
