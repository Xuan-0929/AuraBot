import { randomUUID } from "node:crypto";
import type { PetActivity } from "../../shared/contracts/pet";
import type { EventLogService } from "../telemetry/eventLogService";
import type { MemoryService } from "../memory/memoryService";
import type { ProfileService } from "../profiles/profileService";
import type { Soul } from "../../shared/schemas/profile";
import type { Task } from "../../shared/schemas/task";
import type { TaskService } from "../tasks/taskService";
import type { VoiceAdapter } from "../voice/voiceAdapter";

export interface ProactiveTriggerEngineDependencies {
  profileService: ProfileService;
  memoryService: MemoryService;
  taskService: TaskService;
  eventLogService: EventLogService;
  voiceAdapter: VoiceAdapter;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastTaskChanged?(task: Task): void;
}

export interface ProactiveTriggerEngineOptions {
  pollIntervalMs?: number;
  followUpLookaheadMs?: number;
  idleThresholdMs?: number;
  idleCooldownMs?: number;
  runImmediately?: boolean;
}

const DEFAULT_POLL_INTERVAL_MS = 10_000;
const DEFAULT_IDLE_THRESHOLD_MS = 10 * 60_000;
const DEFAULT_IDLE_COOLDOWN_MS = 30 * 60_000;

export class ProactiveTriggerEngine {
  private timer: ReturnType<typeof setInterval> | undefined;
  private pollIntervalMs: number;
  private followUpLookaheadMs: number;
  private idleThresholdMs: number;
  private idleCooldownMs: number;
  private lastUserActivityAtMs: number;
  private lastIdleNudgeAtMs: number | undefined;

  constructor(
    private readonly dependencies: ProactiveTriggerEngineDependencies,
    options: ProactiveTriggerEngineOptions = {}
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.followUpLookaheadMs = options.followUpLookaheadMs ?? 0;
    this.idleThresholdMs = options.idleThresholdMs ?? DEFAULT_IDLE_THRESHOLD_MS;
    this.idleCooldownMs = options.idleCooldownMs ?? DEFAULT_IDLE_COOLDOWN_MS;
    this.lastUserActivityAtMs = Date.now();

    if (options.runImmediately) {
      this.runScheduledCheck();
    }
  }

  updateOptions(options: ProactiveTriggerEngineOptions): void {
    const wasRunning = Boolean(this.timer);

    if (wasRunning) {
      this.stop();
    }

    this.pollIntervalMs = options.pollIntervalMs ?? this.pollIntervalMs;
    this.followUpLookaheadMs = options.followUpLookaheadMs ?? this.followUpLookaheadMs;
    this.idleThresholdMs = options.idleThresholdMs ?? this.idleThresholdMs;
    this.idleCooldownMs = options.idleCooldownMs ?? this.idleCooldownMs;

    if (wasRunning) {
      this.start();
    }
  }

  start(): void {
    if (this.timer) {
      return;
    }

    this.timer = setInterval(() => this.runScheduledCheck(), this.pollIntervalMs);
  }

  stop(): void {
    if (!this.timer) {
      return;
    }

    clearInterval(this.timer);
    this.timer = undefined;
  }

  recordUserActivity(nowIso = new Date().toISOString()): void {
    const nowMs = parseIsoToMs(nowIso) ?? Date.now();
    this.lastUserActivityAtMs = nowMs;

    this.dependencies.eventLogService.record("proactive.user_activity_recorded", {
      at: nowIso
    });
  }

  async checkTriggers(nowIso = new Date().toISOString()): Promise<PetActivity[]> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const nowMs = parseIsoToMs(nowIso) ?? Date.now();
    const emitted: PetActivity[] = [];

    emitted.push(...(await this.checkDueFollowUps({ profileId: profile.id, nowIso })));
    emitted.push(...(await this.checkDueReminderTasks({ profileId: profile.id, nowIso, soul: profile.soul })));

    const idleActivity = await this.checkIdleNudge({
      profileId: profile.id,
      nowIso,
      nowMs,
      soul: profile.soul
    });

    if (idleActivity) {
      emitted.push(idleActivity);
    }

    return emitted;
  }

  private async checkDueFollowUps(input: { profileId: string; nowIso: string }): Promise<PetActivity[]> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const dueFollowUps = this.dependencies.memoryService.retrieveDueFollowUps(
      input.profileId,
      input.nowIso,
      this.followUpLookaheadMs
    );

    this.dependencies.eventLogService.record("proactive.trigger_evaluated", {
      trigger: "memory.follow_up",
      profileId: input.profileId,
      candidateCount: dueFollowUps.length
    });

    if (dueFollowUps.length === 0) {
      return [];
    }

    const activity: PetActivity = {
      id: randomUUID(),
      kind: "memory_follow_up",
      message: await this.dependencies.voiceAdapter.render({
        scene: "memory.follow_up",
        channel: "pet_notice",
        soul: profile.soul,
        facts: {
          contents: dueFollowUps.map((memory) => memory.content)
        }
      }),
      createdAt: input.nowIso
    };
    const nodeIds = dueFollowUps.map((memory) => memory.id);

    this.emitActivity(activity, {
      trigger: "memory.follow_up",
      profileId: input.profileId,
      nodeIds
    });
    this.dependencies.memoryService.completeFollowUps(input.profileId, nodeIds);
    this.dependencies.eventLogService.record("memory.follow_up_triggered", {
      profileId: input.profileId,
      trigger: "proactive_engine",
      nodeIds
    });

    return [activity];
  }

  private async checkDueReminderTasks(input: {
    profileId: string;
    nowIso: string;
    soul?: Soul;
  }): Promise<PetActivity[]> {
    const dueTasks = this.dependencies.taskService.listDueReminderTasks(input.profileId, input.nowIso);

    this.dependencies.eventLogService.record("proactive.trigger_evaluated", {
      trigger: "task.time_reminder",
      profileId: input.profileId,
      candidateCount: dueTasks.length
    });

    if (dueTasks.length === 0) {
      return [];
    }

    const activities: PetActivity[] = [];

    for (const task of dueTasks) {
      const message = await this.dependencies.voiceAdapter.render({
        scene: "task.reminder_due",
        channel: "pet_notice",
        soul: input.soul,
        facts: {
          taskTitle: task.title,
          dueAt: task.dueAt
        }
      });
      const activity: PetActivity = {
        id: randomUUID(),
        kind: "task_reminder",
        message,
        createdAt: input.nowIso
      };

      this.emitActivity(activity, {
        trigger: "task.time_reminder",
        profileId: input.profileId,
        taskId: task.id,
        dueAt: task.dueAt ?? null
      });
      const completedTask = this.dependencies.taskService.completeReminderTask({
        profileId: input.profileId,
        taskId: task.id,
        summary: message
      });
      if (completedTask) {
        this.dependencies.broadcastTaskChanged?.(completedTask);
      }
      activities.push(activity);
    }

    return activities;
  }

  private async checkIdleNudge(input: {
    profileId: string;
    nowIso: string;
    nowMs: number;
    soul?: Soul;
  }): Promise<PetActivity | undefined> {
    const idleForMs = input.nowMs - this.lastUserActivityAtMs;
    const activeTasks = this.dependencies.taskService
      .listTasksForProfile(input.profileId)
      .filter(
        (task) =>
          task.kind === "result_output" &&
          (task.status === "queued" || task.status === "running" || task.status === "waiting")
      );
    const cooldownRemainingMs =
      this.lastIdleNudgeAtMs === undefined
        ? 0
        : Math.max(this.idleCooldownMs - (input.nowMs - this.lastIdleNudgeAtMs), 0);

    this.dependencies.eventLogService.record("proactive.trigger_evaluated", {
      trigger: "idle_nudge",
      profileId: input.profileId,
      idleForMs,
      activeTaskCount: activeTasks.length,
      cooldownRemainingMs
    });

    if (idleForMs < this.idleThresholdMs) {
      this.recordSuppressed("idle_nudge", input.profileId, "idle_threshold", { idleForMs });
      return undefined;
    }

    if (cooldownRemainingMs > 0) {
      this.recordSuppressed("idle_nudge", input.profileId, "cooldown", { cooldownRemainingMs });
      return undefined;
    }

    if (activeTasks.length > 0) {
      this.recordSuppressed("idle_nudge", input.profileId, "active_task", {
        activeTaskIds: activeTasks.map((task) => task.id)
      });
      return undefined;
    }

    const idleMinutes = Math.max(Math.floor(idleForMs / 60_000), 1);
    const activity: PetActivity = {
      id: randomUUID(),
      kind: "proactive_idle",
      message: await this.dependencies.voiceAdapter.render({
        scene: "proactive.idle_nudge",
        channel: "pet_notice",
        soul: input.soul,
        facts: {
          idleMinutes
        }
      }),
      createdAt: input.nowIso
    };

    this.lastIdleNudgeAtMs = input.nowMs;
    this.emitActivity(activity, {
      trigger: "idle_nudge",
      profileId: input.profileId,
      idleForMs
    });

    return activity;
  }

  private emitActivity(activity: PetActivity, payload: Record<string, unknown>): void {
    this.dependencies.broadcastPetActivity(activity);
    this.dependencies.eventLogService.record("proactive.message_emitted", {
      ...payload,
      activityId: activity.id,
      activityKind: activity.kind,
      channel: "pet_notice"
    });
  }

  private recordSuppressed(
    trigger: string,
    profileId: string,
    reason: string,
    extra: Record<string, unknown> = {}
  ): void {
    this.dependencies.eventLogService.record("proactive.suppressed", {
      trigger,
      profileId,
      reason,
      ...extra
    });
  }

  private runScheduledCheck(): void {
    this.checkTriggers().catch((error: unknown) => {
      this.dependencies.eventLogService.record("proactive.scheduler_failed", {
        message: error instanceof Error ? error.message : String(error)
      });
    });
  }
}

function parseIsoToMs(value: string): number | undefined {
  const parsed = new Date(value).getTime();

  return Number.isFinite(parsed) ? parsed : undefined;
}
