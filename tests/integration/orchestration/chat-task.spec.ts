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

  it("uses task hints in the main chat to query, revise, and interrupt a specific task", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    const createdMeeting = await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const createdWeekly = await orchestrator.sendMessage({ content: "帮我生成一份周报初稿。" });

    expect(createdWeekly.createdTask?.title).toBe("帮我生成一份周报初稿。");

    const queryMeeting = await orchestrator.sendMessage({ content: "会议那个任务做到哪了？" });
    const reviseMeeting = await orchestrator.sendMessage({ content: "把会议任务改成整理后天路演的清单。" });
    const interruptWeekly = await orchestrator.sendMessage({ content: "停掉周报任务。" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);
    const meetingTask = tasks.find((task) => task.id === createdMeeting.createdTask?.id);
    const weeklyTask = tasks.find((task) => task.id === createdWeekly.createdTask?.id);

    expect(queryMeeting.assistantMessage?.content).toContain("明天会议");
    expect(reviseMeeting.updatedTask?.id).toBe(createdMeeting.createdTask?.id);
    expect(reviseMeeting.updatedTask?.title).toBe("整理后天路演的清单。");
    expect(interruptWeekly.updatedTask?.id).toBe(createdWeekly.createdTask?.id);
    expect(interruptWeekly.updatedTask?.status).toBe("interrupted");
    expect(meetingTask?.title).toBe("整理后天路演的清单。");
    expect(meetingTask?.status).toBe("queued");
    expect(weeklyTask?.status).toBe("interrupted");
  });
});
