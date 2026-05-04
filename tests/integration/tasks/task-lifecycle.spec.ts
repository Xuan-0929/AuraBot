import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("task lifecycle", () => {
  it("creates a queued child task session from a main chat request", () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);

    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我整理一份明天会议的清单。"
    });
    const tasks = services.taskService.listTasksForProfile(profile.id);
    const taskSession = services.sessionRepository.getSession(task.sessionId);
    const eventLogs = services.eventLogService.list(20);

    expect(task.status).toBe("queued");
    expect(task.parentSessionId).toBe(mainSession.id);
    expect(task.latestProgress).toContain("queued");
    expect(tasks).toHaveLength(1);
    expect(taskSession?.kind).toBe("task");
    expect(taskSession?.parentSessionId).toBe(mainSession.id);
    expect(taskSession?.depth).toBe(1);
    expect(eventLogs.some((log) => log.eventType === "task.created")).toBe(true);
  });

  it("interrupts and revises the latest active task through service methods", () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我整理一份明天会议的清单。"
    });

    const interrupted = services.taskService.interruptLatestActiveTask({
      profileId: profile.id,
      reason: "用户在主聊天线程要求停掉"
    });
    const revised = services.taskService.reviseTaskFromChat({
      profileId: profile.id,
      taskId: task.id,
      title: "改成整理后天路演的清单。"
    });
    const tasks = services.taskService.listTasksForProfile(profile.id);
    const eventLogs = services.eventLogService.list(20);

    expect(interrupted?.status).toBe("interrupted");
    expect(revised?.status).toBe("queued");
    expect(revised?.title).toContain("后天路演");
    expect(tasks[0]?.title).toContain("后天路演");
    expect(eventLogs.some((log) => log.eventType === "task.interrupted")).toBe(true);
    expect(eventLogs.some((log) => log.eventType === "task.revised")).toBe(true);
  });

  it("runs the latest queued task through progress events to completion", async () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我整理一份明天会议的清单。"
    });

    const completed = await services.taskService.runLatestQueuedTask(profile.id);
    const events = services.taskService.listTaskEvents(task.id);
    const eventLogs = services.eventLogService.list(20);

    expect(completed?.status).toBe("completed");
    expect(completed?.latestProgress).toContain("完成");
    expect(events.map((event) => event.eventType)).toEqual([
      "task.running",
      "task.progress",
      "task.completed"
    ]);
    expect(events[1]?.summary).toContain("明天会议");
    expect(eventLogs.some((log) => log.eventType === "task.completed")).toBe(true);
  });
});
