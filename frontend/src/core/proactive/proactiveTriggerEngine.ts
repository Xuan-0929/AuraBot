/**
 * 调度可选的旧 Memory follow-up 与独立 idle nudge 主动触达。
 *
 * 所有权切换后生产环境关闭旧 Memory follow-up 的读取/完成写入，但保留 idle nudge；
 * 引擎不读取 Task 状态、不调度定时 Task，也不会因历史 active Task 抑制 idle nudge。
 */

import { randomUUID } from "node:crypto";
import type { PetActivity } from "@shared/contracts/pet";
import type { Soul } from "@shared/schemas/profile";
import type { MemoryService } from "../memory/memoryService";
import type { ProfileService } from "../profiles/profileService";
import type { EventLogService } from "../telemetry/eventLogService";
import type { VoiceAdapter } from "../voice/voiceAdapter";

export interface ProactiveTriggerEngineDependencies {
  profileService: ProfileService;
  memoryService: MemoryService;
  eventLogService: EventLogService;
  voiceAdapter: VoiceAdapter;
  broadcastPetActivity(activity: PetActivity): void;
  deliverSystemNotification?(input: { kind: PetActivity["kind"]; message: string }): void;
}

export interface ProactiveTriggerEngineOptions {
  memoryFollowUpsEnabled?: boolean;
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
  private memoryFollowUpsEnabled: boolean;
  private lastUserActivityAtMs = Date.now();
  private lastIdleNudgeAtMs: number | undefined;

  constructor(
    private readonly dependencies: ProactiveTriggerEngineDependencies,
    options: ProactiveTriggerEngineOptions = {}
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.memoryFollowUpsEnabled = options.memoryFollowUpsEnabled ?? true;
    this.followUpLookaheadMs = options.followUpLookaheadMs ?? 0;
    this.idleThresholdMs = options.idleThresholdMs ?? DEFAULT_IDLE_THRESHOLD_MS;
    this.idleCooldownMs = options.idleCooldownMs ?? DEFAULT_IDLE_COOLDOWN_MS;
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
    this.memoryFollowUpsEnabled = options.memoryFollowUpsEnabled ?? this.memoryFollowUpsEnabled;
    this.followUpLookaheadMs = options.followUpLookaheadMs ?? this.followUpLookaheadMs;
    this.idleThresholdMs = options.idleThresholdMs ?? this.idleThresholdMs;
    this.idleCooldownMs = options.idleCooldownMs ?? this.idleCooldownMs;
    if (wasRunning) {
      this.start();
    }
  }

  start(): void {
    this.timer ??= setInterval(() => this.runScheduledCheck(), this.pollIntervalMs);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  recordUserActivity(nowIso = new Date().toISOString()): void {
    this.lastUserActivityAtMs = parseIsoToMs(nowIso) ?? Date.now();
    this.dependencies.eventLogService.record("proactive.user_activity_recorded", { at: nowIso });
  }

  async checkTriggers(nowIso = new Date().toISOString()): Promise<PetActivity[]> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const emitted = this.memoryFollowUpsEnabled
      ? await this.checkDueFollowUps(profile.id, nowIso)
      : [];
    const idle = await this.checkIdleNudge({
      profileId: profile.id,
      soul: profile.soul,
      nowIso,
      nowMs: parseIsoToMs(nowIso) ?? Date.now()
    });
    return idle ? [...emitted, idle] : emitted;
  }

  private async checkDueFollowUps(profileId: string, nowIso: string): Promise<PetActivity[]> {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const due = this.dependencies.memoryService.retrieveDueFollowUps(
      profileId,
      nowIso,
      this.followUpLookaheadMs
    );
    this.dependencies.eventLogService.record("proactive.trigger_evaluated", {
      trigger: "memory.follow_up",
      profileId,
      candidateCount: due.length
    });
    if (due.length === 0) {
      return [];
    }
    const activity: PetActivity = {
      id: randomUUID(),
      kind: "memory_follow_up",
      message: await this.dependencies.voiceAdapter.render({
        scene: "memory.follow_up",
        channel: "pet_notice",
        soul: profile.soul,
        facts: { contents: due.map((memory) => memory.content) }
      }),
      createdAt: nowIso
    };
    const nodeIds = due.map((memory) => memory.id);
    this.emitActivity(activity, { trigger: "memory.follow_up", profileId, nodeIds });
    this.dependencies.memoryService.completeFollowUps(profileId, nodeIds);
    this.dependencies.eventLogService.record("memory.follow_up_triggered", {
      profileId,
      trigger: "proactive_engine",
      nodeIds
    });
    return [activity];
  }

  private async checkIdleNudge(input: {
    profileId: string;
    soul?: Soul;
    nowIso: string;
    nowMs: number;
  }): Promise<PetActivity | undefined> {
    const idleForMs = input.nowMs - this.lastUserActivityAtMs;
    const cooldownRemainingMs =
      this.lastIdleNudgeAtMs === undefined
        ? 0
        : Math.max(this.idleCooldownMs - (input.nowMs - this.lastIdleNudgeAtMs), 0);
    this.dependencies.eventLogService.record("proactive.trigger_evaluated", {
      trigger: "idle_nudge",
      profileId: input.profileId,
      idleForMs,
      cooldownRemainingMs
    });
    if (idleForMs < this.idleThresholdMs) {
      this.recordSuppressed(input.profileId, "idle_threshold", { idleForMs });
      return undefined;
    }
    if (cooldownRemainingMs > 0) {
      this.recordSuppressed(input.profileId, "cooldown", { cooldownRemainingMs });
      return undefined;
    }
    const activity: PetActivity = {
      id: randomUUID(),
      kind: "proactive_idle",
      message: await this.dependencies.voiceAdapter.render({
        scene: "proactive.idle_nudge",
        channel: "pet_notice",
        soul: input.soul,
        facts: { idleMinutes: Math.max(Math.floor(idleForMs / 60_000), 1) }
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
    this.dependencies.deliverSystemNotification?.({
      kind: activity.kind,
      message: activity.message
    });
    this.dependencies.eventLogService.record("proactive.message_emitted", {
      ...payload,
      activityId: activity.id,
      activityKind: activity.kind,
      channel: "pet_notice"
    });
  }

  private recordSuppressed(
    profileId: string,
    reason: string,
    extra: Record<string, unknown>
  ): void {
    this.dependencies.eventLogService.record("proactive.suppressed", {
      trigger: "idle_nudge",
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
