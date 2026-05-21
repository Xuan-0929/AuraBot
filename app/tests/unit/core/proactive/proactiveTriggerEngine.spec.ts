import { afterEach, describe, expect, it } from "vitest";
import type { PetActivity } from "../../../../packages/shared/src/contracts/pet";
import { ProactiveTriggerEngine } from "../../../../packages/core-legacy/src/proactive/proactiveTriggerEngine";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("ProactiveTriggerEngine", () => {
  it("emits due follow-up reminders as pet activity without writing chat history", async () => {
    const services = createTestServices("aurabot-proactive-follow-up-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];
    const notifications: Array<{ kind: PetActivity["kind"]; message: string }> = [];

    await services.memoryService.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: mainSession.id,
      node: {
        type: "follow_up",
        content: "提醒用户准备今晚的算法面试",
        importance: 0.88,
        startsAt: "2026-05-05T20:00:00.000+08:00"
      },
      edges: []
    });

    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        taskService: services.taskService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: (activity) => activities.push(activity),
        deliverSystemNotification: (input) => notifications.push(input)
      },
      {
        idleThresholdMs: 60_000
      }
    );

    engine.recordUserActivity("2026-05-05T20:00:59.000+08:00");

    const beforeMessages = services.messageRepository.listBySession(mainSession.id);
    const result = await engine.checkTriggers("2026-05-05T20:01:00.000+08:00");
    const afterMessages = services.messageRepository.listBySession(mainSession.id);
    const followUp = services.memoryService
      .retrieveForProfile(profile.id)
      .nodes.find((node) => node.type === "follow_up");
    const eventLogs = services.eventLogService.list(20);

    expect(result).toHaveLength(1);
    expect(activities).toHaveLength(1);
    expect(notifications).toEqual([
      expect.objectContaining({
        kind: "memory_follow_up",
        message: expect.stringContaining("算法面试")
      })
    ]);
    expect(activities[0]).toMatchObject({
      kind: "memory_follow_up",
      message: expect.stringContaining("算法面试")
    });
    expect(followUp?.state).toBe("completed");
    expect(afterMessages).toEqual(beforeMessages);
    expect(eventLogs).toContainEqual(
      expect.objectContaining({
        eventType: "proactive.message_emitted",
        payload: expect.objectContaining({
          trigger: "memory.follow_up",
          channel: "pet_notice"
        })
      })
    );
  });

  it("emits due time reminder tasks as pet activity and completes them", async () => {
    const services = createTestServices("aurabot-proactive-reminder-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];
    const notifications: Array<{ kind: PetActivity["kind"]; message: string }> = [];
    const changedTasks: Array<NonNullable<ReturnType<typeof services.taskService.getTaskById>>> = [];
    const reminderTask = services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "喝水",
      kind: "time_reminder",
      dueAt: "2026-05-05T20:00:00.000+08:00"
    });

    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        taskService: services.taskService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: (activity) => activities.push(activity),
        broadcastTaskChanged: (task) => changedTasks.push(task),
        deliverSystemNotification: (input) => notifications.push(input)
      },
      {
        idleThresholdMs: 60_000
      }
    );

    engine.recordUserActivity("2026-05-05T20:00:59.000+08:00");

    const beforeMessages = services.messageRepository.listBySession(mainSession.id);
    const result = await engine.checkTriggers("2026-05-05T20:01:00.000+08:00");
    const afterMessages = services.messageRepository.listBySession(mainSession.id);
    const completedTask = services.taskService.getTaskById(reminderTask.id);
    const eventLogs = services.eventLogService.list(30);

    expect(result).toHaveLength(1);
    expect(activities).toHaveLength(1);
    expect(notifications).toEqual([
      expect.objectContaining({
        kind: "task_reminder",
        message: expect.stringContaining("喝水")
      })
    ]);
    expect(activities[0]).toMatchObject({
      kind: "task_reminder",
      message: expect.stringContaining("喝水")
    });
    expect(completedTask).toMatchObject({
      status: "completed",
      kind: "time_reminder"
    });
    expect(changedTasks).toContainEqual(
      expect.objectContaining({
        id: reminderTask.id,
        status: "completed",
        kind: "time_reminder"
      })
    );
    expect(afterMessages).toEqual(beforeMessages);
    expect(eventLogs).toContainEqual(
      expect.objectContaining({
        eventType: "proactive.message_emitted",
        payload: expect.objectContaining({
          trigger: "task.time_reminder",
          channel: "pet_notice"
        })
      })
    );
  });

  it("marks heavily overdue reminder tasks as missed in the reminder copy", async () => {
    const services = createTestServices("aurabot-proactive-missed-reminder-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];
    services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "喝水",
      kind: "time_reminder",
      dueAt: "2026-05-05T09:00:00.000+08:00"
    });

    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        taskService: services.taskService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: (activity) => activities.push(activity)
      },
      {
        pollIntervalMs: 10_000,
        idleThresholdMs: 60_000
      }
    );

    const result = await engine.checkTriggers("2026-05-05T10:00:00.000+08:00");

    expect(result[0]?.message).toContain("刚才错过了这个提醒");
    expect(activities[0]?.message).toContain("刚才错过了这个提醒");
  });

  it("emits an idle nudge only to the pet bubble and respects cooldown", async () => {
    const services = createTestServices("aurabot-proactive-idle-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];
    const notifications: Array<{ kind: PetActivity["kind"]; message: string }> = [];
    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        taskService: services.taskService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: (activity) => activities.push(activity),
        deliverSystemNotification: (input) => notifications.push(input)
      },
      {
        idleThresholdMs: 1_000,
        idleCooldownMs: 10_000
      }
    );

    engine.recordUserActivity("2026-05-05T20:00:00.000+08:00");

    const first = await engine.checkTriggers("2026-05-05T20:00:02.000+08:00");
    const second = await engine.checkTriggers("2026-05-05T20:00:03.000+08:00");
    const messages = services.messageRepository.listBySession(mainSession.id);
    const eventLogs = services.eventLogService.list(30);

    expect(first).toHaveLength(1);
    expect(first[0]).toMatchObject({
      kind: "proactive_idle",
      message: expect.stringContaining("我在")
    });
    expect(second).toEqual([]);
    expect(activities).toHaveLength(1);
    expect(notifications).toEqual([
      expect.objectContaining({
        kind: "proactive_idle",
        message: expect.stringContaining("我在")
      })
    ]);
    expect(messages).toEqual([]);
    expect(eventLogs).toContainEqual(
      expect.objectContaining({
        eventType: "proactive.suppressed",
        payload: expect.objectContaining({
          trigger: "idle_nudge",
          reason: "cooldown"
        })
      })
    );
  });

  it("suppresses idle nudges while a task is active", async () => {
    const services = createTestServices("aurabot-proactive-task-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];
    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        taskService: services.taskService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: (activity) => activities.push(activity)
      },
      {
        idleThresholdMs: 1_000
      }
    );

    services.taskService.createTaskFromChat({
      profileId: profile.id,
      parentSessionId: mainSession.id,
      title: "帮我写一份周报。"
    });
    engine.recordUserActivity("2026-05-05T20:00:00.000+08:00");

    const result = await engine.checkTriggers("2026-05-05T20:00:02.000+08:00");
    const eventLogs = services.eventLogService.list(30);

    expect(result).toEqual([]);
    expect(activities).toEqual([]);
    expect(eventLogs).toContainEqual(
      expect.objectContaining({
        eventType: "proactive.suppressed",
        payload: expect.objectContaining({
          trigger: "idle_nudge",
          reason: "active_task"
        })
      })
    );
  });
});
