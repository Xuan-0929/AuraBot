import { randomUUID } from "node:crypto";
import type { PetActivity } from "../../shared/contracts/pet";
import type { EventLogService } from "../telemetry/eventLogService";
import type { MemoryService } from "./memoryService";
import type { ProfileService } from "../profiles/profileService";

export interface MemoryFollowUpSchedulerDependencies {
  profileService: ProfileService;
  memoryService: MemoryService;
  eventLogService: EventLogService;
  broadcastPetActivity(activity: PetActivity): void;
}

export interface MemoryFollowUpSchedulerOptions {
  pollIntervalMs?: number;
  lookaheadMs?: number;
  runImmediately?: boolean;
}

const DEFAULT_POLL_INTERVAL_MS = 60_000;

export class MemoryFollowUpScheduler {
  private timer: ReturnType<typeof setInterval> | undefined;
  private readonly pollIntervalMs: number;
  private readonly lookaheadMs: number;

  constructor(
    private readonly dependencies: MemoryFollowUpSchedulerDependencies,
    options: MemoryFollowUpSchedulerOptions = {}
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    this.lookaheadMs = options.lookaheadMs ?? 0;

    if (options.runImmediately) {
      this.runScheduledCheck();
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

  checkDueFollowUps(nowIso = new Date().toISOString()): PetActivity[] {
    const profile = this.dependencies.profileService.getOrSeedActiveProfile();
    const dueFollowUps = this.dependencies.memoryService.retrieveDueFollowUps(
      profile.id,
      nowIso,
      this.lookaheadMs
    );

    if (dueFollowUps.length === 0) {
      return [];
    }

    const activity: PetActivity = {
      id: randomUUID(),
      kind: "memory_follow_up",
      message: formatFollowUpActivityMessage(dueFollowUps.map((memory) => memory.content)),
      createdAt: nowIso
    };
    const nodeIds = dueFollowUps.map((memory) => memory.id);

    this.dependencies.broadcastPetActivity(activity);
    this.dependencies.memoryService.completeFollowUps(profile.id, nodeIds);
    this.dependencies.eventLogService.record("memory.follow_up_triggered", {
      profileId: profile.id,
      trigger: "scheduler",
      nodeIds
    });

    return [activity];
  }

  private runScheduledCheck(): void {
    try {
      this.checkDueFollowUps();
    } catch (error) {
      this.dependencies.eventLogService.record("memory.follow_up_scheduler_failed", {
        message: error instanceof Error ? error.message : String(error)
      });
    }
  }
}

function formatFollowUpActivityMessage(contents: string[]): string {
  const cleanedContents = contents.map(cleanFollowUpContent).filter(Boolean);

  if (cleanedContents.length === 0) {
    return "我提醒你一下：有一件之前记下的事到时间了。";
  }

  return `我提醒你一下：${cleanedContents.join("；")}`;
}

function cleanFollowUpContent(content: string): string {
  return content
    .replace(/^需要跟进或提醒用户[:：]\s*/, "")
    .replace(/^提醒用户\s*/, "")
    .trim();
}
