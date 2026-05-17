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
    expect(result.assistantMessage?.content).toContain("开始弄");
    expect(result.assistantMessage?.content).toContain("明天会议");
    expect(result.assistantMessage?.content).not.toContain("排队");
    expect(result.assistantMessage?.content).not.toContain("任务");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.status).toBe("queued");
    expect(memoryContext.nodes).toHaveLength(0);
  });

  it("treats writing a reading note as a task instead of waiting for more chat", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    const result = await orchestrator.sendMessage({ content: "帮我写一份实践论的读书笔记" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(result.agentStatus).toBe("tasking");
    expect(result.gateDecision?.inputKind).toBe("task");
    expect(result.gateDecision?.replyDecision).toBe("reply");
    expect(result.assistantMessage?.content).toContain("开始弄");
    expect(result.assistantMessage?.content).toContain("实践论");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.title).toContain("实践论");
    expect(tasks[0]?.status).toBe("queued");
  });

  it.each(["做一份读书笔记", "写一份GitHub周报"])(
    "treats direct creation request %s as a task without requiring a helper prefix",
    async (content) => {
      const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

      const result = await orchestrator.sendMessage({ content });
      const profile = services.profileService.getOrSeedActiveProfile();
      const tasks = services.taskService.listTasksForProfile(profile.id);

      expect(result.agentStatus).toBe("tasking");
      expect(result.gateDecision?.inputKind).toBe("task");
      expect(result.gateDecision?.replyDecision).toBe("reply");
      expect(result.assistantMessage?.content).toContain("开始弄");
      expect(tasks).toHaveLength(1);
      expect(tasks[0]?.title).toBe(content);
      expect(tasks[0]?.status).toBe("queued");
    }
  );

  it("lets the main chat query, interrupt, and revise the latest task", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-");

    await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });
    const interrupt = await orchestrator.sendMessage({ content: "先停掉这个任务。" });
    const revise = await orchestrator.sendMessage({ content: "改成整理后天路演的清单。" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(query.gateDecision?.inputKind).toBe("task_control");
    expect(query.assistantMessage?.content).toContain("先替你记在旁边了");
    expect(query.assistantMessage?.content).toContain("明天会议");
    expect(query.assistantMessage?.content).toContain("直接跟我说一声就行");
    expect(interrupt.updatedTask?.status).toBe("interrupted");
    expect(interrupt.assistantMessage?.content).toContain("先在这里停一下");
    expect(interrupt.assistantMessage?.content).toContain("新的要求");
    expect(revise.updatedTask?.status).toBe("queued");
    expect(revise.updatedTask?.title).toContain("后天路演");
    expect(revise.assistantMessage?.content).toContain("后天路演");
    expect(tasks).toHaveLength(1);
    expect(tasks[0]?.title).toContain("后天路演");
  });

  it("starts the latest queued task from main chat without waiting for completion", async () => {
    const services = createTestServices("aurabot-chat-task-");
    const taskRepository = new TaskRepository(services.database);
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async () => new Promise(() => undefined)
      }
    );
    const orchestrator = new ChatOrchestrator({
      ...services,
      taskService
    });

    await orchestrator.sendMessage({ content: "帮我整理一份明天会议的清单。" });
    const start = await orchestrator.sendMessage({ content: "开始做这个任务。" });
    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(start.updatedTask?.status).toBe("running");
    expect(start.assistantMessage?.content).toContain("明天会议");
    expect(start.assistantMessage?.content).toContain("已经在弄了");
    expect(query.assistantMessage?.content).toContain("已经在弄了");
    expect(query.assistantMessage?.content).not.toContain("completed");
    expect(tasks[0]?.status).toBe("running");
  });

  it("describes a running child agent task without exposing execution details", async () => {
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

    expect(query.assistantMessage?.content).toContain("已经在弄了");
    expect(query.assistantMessage?.content).toContain("GitHub 周报");
    expect(query.assistantMessage?.content).toContain("已经开始处理");
    expect(query.assistantMessage?.content).toContain("回来跟你说");
    expect(query.assistantMessage?.content).not.toContain("running");
    expect(query.assistantMessage?.content).not.toContain("Codex");
    expect(query.assistantMessage?.content).not.toContain("/Users/xuan/project");
    expect(query.assistantMessage?.content).not.toContain("/Users/xuan/output/task.md");
    expect(query.assistantMessage?.content).not.toContain("sandbox");
  });

  it("reports an already failed child task back through the main chat", async () => {
    const services = createTestServices("aurabot-chat-task-");
    const taskRepository = new TaskRepository(services.database);
    const orchestrator = new ChatOrchestrator({
      ...services,
      taskService: services.taskService
    });

    const created = await orchestrator.sendMessage({ content: "帮我做一份 GitHub 周报。" });
    taskRepository.updateStatus({
      taskId: created.createdTask!.id,
      status: "failed",
      latestProgress: "Codex 子任务失败：codex exited 1"
    });
    const query = await orchestrator.sendMessage({ content: "这个任务做到哪了？" });

    expect(query.assistantMessage?.content).toContain("这次我先卡在");
    expect(query.assistantMessage?.content).toContain("codex exited 1");
    expect(query.assistantMessage?.content).toContain("接着一起改");
  });

  it("retries a failed task from the main chat and starts it in the background", async () => {
    const services = createTestServices("aurabot-chat-task-retry-");
    const taskRepository = new TaskRepository(services.database);
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async () => new Promise(() => undefined)
      }
    );
    const orchestrator = new ChatOrchestrator({
      ...services,
      taskService
    });
    const created = await orchestrator.sendMessage({ content: "帮我做一份 GitHub 周报。" });
    taskRepository.updateStatus({
      taskId: created.createdTask!.id,
      status: "failed",
      latestProgress: "Codex 子任务失败：codex exited 1"
    });

    const retry = await orchestrator.sendMessage({ content: "重试周报任务" });

    expect(retry.updatedTask?.id).toBe(created.createdTask?.id);
    expect(retry.updatedTask?.status).toBe("running");
    expect(retry.assistantMessage?.content).toContain("已经在弄了");
  });

  it("opens result attachments and deletes completed tasks from chat control", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-chat-task-open-delete-");
    const taskRepository = new TaskRepository(services.database);
    const created = await orchestrator.sendMessage({ content: "帮我做一份 GitHub 周报。" });
    taskRepository.appendEvent({
      taskId: created.createdTask!.id,
      eventType: "task.agent.progress",
      summary: "写入周报文件",
      payload: {
        fileChange: {
          path: "/tmp/aura-weekly.md"
        }
      }
    });
    taskRepository.updateStatus({
      taskId: created.createdTask!.id,
      status: "completed",
      latestProgress: "已完成：GitHub 周报"
    });

    const open = await orchestrator.sendMessage({ content: "打开周报结果" });
    const remove = await orchestrator.sendMessage({ content: "删除已完成的周报任务" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const tasks = services.taskService.listTasksForProfile(profile.id);

    expect(open.assistantMessage?.attachments?.[0]).toMatchObject({
      kind: "file",
      path: "/tmp/aura-weekly.md"
    });
    expect(remove.deletedTaskId).toBe(created.createdTask?.id);
    expect(remove.assistantMessage?.content).toContain("移除了");
    expect(tasks.find((task) => task.id === created.createdTask?.id)).toBeUndefined();
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
