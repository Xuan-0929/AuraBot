import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createServices } from "../../../packages/core-legacy/src/bootstrap/createServices";
import { TaskRepository } from "../../../packages/core-legacy/src/storage/repositories/taskRepository";
import { TaskService } from "../../../packages/core-legacy/src/tasks/taskService";
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

  it("starts a queued task in the background and exposes running progress immediately", async () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我整理一份明天会议的清单。"
    });

    const started = services.taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id
    });

    expect(started?.status).not.toBe("queued");
    expect(started?.latestProgress).not.toBe("Task queued from main chat.");
  });

  it("does not start the same queued background task more than once", async () => {
    const services = createTestServices("aurabot-task-");
    const taskRepository = new TaskRepository(services.database);
    let runCount = 0;
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async () => {
          runCount += 1;

          return new Promise(() => undefined);
        }
      }
    );
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });

    const firstStart = taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id
    });
    const secondStart = taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id
    });

    expect(firstStart?.status).toBe("running");
    expect(secondStart?.status).toBe("running");
    expect(runCount).toBe(1);
  });

  it("notifies callers when a background task reports progress", async () => {
    const services = createTestServices("aurabot-task-");
    const taskRepository = new TaskRepository(services.database);
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async (task, observer) => {
          const running = taskRepository.updateStatus({
            taskId: task.id,
            status: "running",
            latestProgress: "Codex 子任务开始处理任务。"
          });

          if (running) {
            observer?.onProgress?.(running);
          }

          return taskRepository.updateStatus({
            taskId: task.id,
            status: "completed",
            latestProgress: "已完成：GitHub 周报"
          });
        }
      }
    );
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });
    const progress: string[] = [];

    taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id,
      onProgress: (changedTask) => progress.push(changedTask.latestProgress)
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(progress).toEqual(["Codex 子任务开始处理任务。"]);
  });

  it("cancels the underlying runner when a running background task is interrupted", async () => {
    const services = createTestServices("aurabot-task-");
    const taskRepository = new TaskRepository(services.database);
    let cancelledTaskId: string | undefined;
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async (task, observer) => {
          const running = taskRepository.updateStatus({
            taskId: task.id,
            status: "running",
            latestProgress: "Codex 子任务开始处理任务。"
          });

          if (running) {
            observer?.onProgress?.(running);
          }

          return new Promise(() => undefined);
        },
        cancel: (taskId) => {
          cancelledTaskId = taskId;

          return true;
        }
      }
    );
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });

    taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    const interrupted = taskService.interruptLatestActiveTask({
      profileId: profile.id,
      reason: "用户在主聊天线程要求停掉"
    });

    expect(interrupted?.status).toBe("interrupted");
    expect(cancelledTaskId).toBe(task.id);
  });

  it("cancels an active task from direct console controls", () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份 GitHub 周报。"
    });

    const cancelled = services.taskService.cancelTaskFromConsole({
      profileId: profile.id,
      taskId: task.id
    });
    const eventLogs = services.eventLogService.list(20);

    expect(cancelled?.status).toBe("interrupted");
    expect(cancelled?.latestProgress).toContain("我先停下来了");
    expect(eventLogs.some((log) => log.eventType === "task.cancelled_from_console")).toBe(true);
  });

  it("deletes only settled tasks from the visible task list", async () => {
    const services = createTestServices("aurabot-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const queuedTask = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "排队任务"
    });
    const completedTask = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "已完成任务"
    });

    await services.taskService.runTaskFromChat({
      profileId: profile.id,
      taskId: completedTask.id
    });

    const queuedDeleted = services.taskService.deleteSettledTaskFromConsole({
      profileId: profile.id,
      taskId: queuedTask.id
    });
    const completedDeleted = services.taskService.deleteSettledTaskFromConsole({
      profileId: profile.id,
      taskId: completedTask.id
    });
    const tasks = services.taskService.listTasksForProfile(profile.id);
    const completedEvents = services.taskService.listTaskEvents(completedTask.id);
    const eventLogs = services.eventLogService.list(20);

    expect(queuedDeleted).toBe(false);
    expect(completedDeleted).toBe(true);
    expect(tasks.map((task) => task.id)).toEqual([queuedTask.id]);
    expect(completedEvents).toHaveLength(0);
    expect(eventLogs.some((log) => log.eventType === "task.deleted_from_console")).toBe(true);
  });

  it("recovers orphaned running tasks as failed when services start again", () => {
    const userDataDir = mkdtempSync(join(tmpdir(), "aurabot-task-recovery-"));
    let firstServices = createServices(userDataDir);

    try {
      const profile = firstServices.profileService.getOrSeedActiveProfile();
      const mainSession = firstServices.sessionRepository.getOrCreateMainSession(profile.id);
      const queuedTask = firstServices.taskService.createTaskFromChat({
        profileId: profile.id,
        parentSessionId: mainSession.id,
        title: "排队任务"
      });
      const runningTask = firstServices.taskService.createTaskFromChat({
        profileId: profile.id,
        parentSessionId: mainSession.id,
        title: "运行中任务"
      });
      const waitingTask = firstServices.taskService.createTaskFromChat({
        profileId: profile.id,
        parentSessionId: mainSession.id,
        title: "等待中任务"
      });
      const taskRepository = new TaskRepository(firstServices.database);
      taskRepository.updateStatus({
        taskId: runningTask.id,
        status: "running",
        latestProgress: "Codex 子任务开始处理任务。"
      });
      taskRepository.updateStatus({
        taskId: waitingTask.id,
        status: "waiting",
        latestProgress: "等待外部子任务。"
      });
      firstServices.database.close();

      const recoveredServices = createServices(userDataDir);
      firstServices = recoveredServices;
      const recoveredTasks = recoveredServices.taskService.listTasksForProfile(profile.id);
      const recoveredQueued = recoveredTasks.find((task) => task.id === queuedTask.id);
      const recoveredRunning = recoveredTasks.find((task) => task.id === runningTask.id);
      const recoveredWaiting = recoveredTasks.find((task) => task.id === waitingTask.id);
      const runningEvents = recoveredServices.taskService.listTaskEvents(runningTask.id);
      const eventLogs = recoveredServices.eventLogService.list(20);

      expect(recoveredQueued?.status).toBe("queued");
      expect(recoveredRunning?.status).toBe("failed");
      expect(recoveredRunning?.latestProgress).toContain("上次应用退出");
      expect(recoveredWaiting?.status).toBe("failed");
      expect(runningEvents.at(-1)?.eventType).toBe("task.recovered_orphaned");
      expect(eventLogs.some((log) => log.eventType === "task.recovered_orphaned")).toBe(true);
    } finally {
      firstServices.database.close();
      rmSync(userDataDir, { force: true, recursive: true });
    }
  });

  it("marks background tasks failed when the runner rejects unexpectedly", async () => {
    const services = createTestServices("aurabot-task-");
    const taskRepository = new TaskRepository(services.database);
    const taskService = new TaskService(
      taskRepository,
      services.sessionRepository,
      services.eventLogService,
      {
        run: async () => {
          throw new Error("runner crashed before reporting progress");
        }
      }
    );
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const task = taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我整理一份明天会议的清单。"
    });
    let settledTaskId: string | undefined;

    taskService.runTaskInBackground({
      profileId: profile.id,
      taskId: task.id,
      onSettled: (settledTask) => {
        settledTaskId = settledTask.id;
      }
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    const failed = taskRepository.getById(task.id);

    expect(failed?.status).toBe("failed");
    expect(failed?.latestProgress).toContain("runner crashed");
    expect(settledTaskId).toBe(task.id);
  });

  it("reschedules a queued reminder task without changing its title", () => {
    const services = createTestServices("aurabot-task-reminder-reschedule-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const reminder = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "喝水",
      kind: "time_reminder",
      dueAt: "2026-05-16T12:10:00.000Z"
    });

    const updated = services.taskService.rescheduleReminderFromChat({
      profileId: profile.id,
      taskId: reminder.id,
      dueAt: "2026-05-17T01:00:00.000Z",
      reason: "用户在主聊天线程调整提醒时间"
    });

    expect(updated).toMatchObject({
      id: reminder.id,
      title: "喝水",
      kind: "time_reminder",
      status: "queued",
      dueAt: "2026-05-17T01:00:00.000Z"
    });
    expect(services.taskService.listTaskEvents(reminder.id).at(-1)?.eventType).toBe("task.reminder_rescheduled");
  });
});
