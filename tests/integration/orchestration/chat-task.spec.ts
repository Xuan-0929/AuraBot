import { afterEach, describe, expect, it } from "vitest";
import { ChatOrchestrator } from "../../../src/core/orchestration/chatOrchestrator";
import { TaskRepository } from "../../../src/core/storage/repositories/taskRepository";
import { TaskService } from "../../../src/core/tasks/taskService";
import { cleanupTestServices, createTestOrchestrator, createTestServices } from "../../helpers/testServices";

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
    expect(query.assistantMessage?.content).toContain("最近进展");
    expect(query.assistantMessage?.content).toContain("Task queued from main chat.");
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

  it("reports child agent execution details when querying a running task", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");
    const taskRepository = new TaskRepository(services.database);

    const created = await orchestrator.sendMessage({ content: "帮我做一份 GitHub 周报。" });
    expect(created.createdTask).toBeDefined();
    taskRepository.appendEvent({
      taskId: created.createdTask!.id,
      eventType: "task.agent.started",
      summary: "Codex 子任务已启动：帮我做一份 GitHub 周报。",
      payload: {
        cwd: "/Users/xuan/project",
        outputFile: "/Users/xuan/output/task.md",
        sandbox: "workspace-write"
      }
    });
    taskRepository.updateStatus({
      taskId: created.createdTask!.id,
      status: "running",
      latestProgress: "开始调用 Codex 子任务：帮我做一份 GitHub 周报。"
    });

    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });

    expect(query.assistantMessage?.content).toContain("running");
    expect(query.assistantMessage?.content).toContain("最近事件：Codex 子任务已启动");
    expect(query.assistantMessage?.content).toContain("运行目录：/Users/xuan/project");
    expect(query.assistantMessage?.content).toContain("输出文件：/Users/xuan/output/task.md");
    expect(query.assistantMessage?.content).toContain("sandbox：workspace-write");
    expect(query.assistantMessage?.content).toContain("自动回到主聊天");
  });

  it("reports a child task runner failure back through the main chat", async () => {
    const services = createTestServices("aurabot-chat-task-");
    const taskRepository = new TaskRepository(services.database);
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async (task) =>
          taskRepository.updateStatus({
            taskId: task.id,
            status: "failed",
            latestProgress: "Codex 子任务失败：codex exited 1"
          })
      }
    );
    const orchestrator = new ChatOrchestrator({
      ...services,
      taskService
    });

    await orchestrator.sendMessage({ content: "帮我做一份 GitHub 周报。" });
    const start = await orchestrator.sendMessage({ content: "开始做这个任务。" });

    expect(start.updatedTask?.status).toBe("failed");
    expect(start.assistantMessage?.content).toContain("这个任务跑失败了");
    expect(start.assistantMessage?.content).toContain("codex exited 1");
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
