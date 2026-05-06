import { randomUUID } from "node:crypto";
import type { PetActivity } from "../../shared/contracts/pet";
import type { EventLogService } from "../telemetry/eventLogService";
import type { MemoryService } from "./memoryService";
import type { ProfileService } from "../profiles/profileService";
import type { VoiceAdapter } from "../voice/voiceAdapter";

export interface MemoryFollowUpSchedulerDependencies {
  profileService: ProfileService;
  memoryService: MemoryService;
  eventLogService: EventLogService;
  voiceAdapter: VoiceAdapter;
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

  async checkDueFollowUps(nowIso = new Date().toISOString()): Promise<PetActivity[]> {
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
      message: await this.dependencies.voiceAdapter.render({
        scene: "memory.follow_up",
        channel: "pet_notice",
        soul: profile.soul,
        facts: {
          contents: dueFollowUps.map((memory) => memory.content)
        }
      }),
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
    this.checkDueFollowUps().catch((error: unknown) => {
      this.dependencies.eventLogService.record("memory.follow_up_scheduler_failed", {
        message: error instanceof Error ? error.message : String(error)
      });
    });
  }
}
