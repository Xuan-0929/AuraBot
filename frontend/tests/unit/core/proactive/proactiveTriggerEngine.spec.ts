/**
 * 验证主动引擎只保留 Memory follow-up 与 idle nudge。
 *
 * 测试使用真实 AppServices 和可观察 broadcast，确认无需旧执行服务也能触发与抑制
 * 非 Task 主动活动。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ProactiveTriggerEngine } from "@core/proactive/proactiveTriggerEngine";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("ProactiveTriggerEngine", () => {
  it("emits an idle nudge without consulting Task state", async () => {
    const services = createTestServices("aurabot-proactive-");
    const broadcastPetActivity = vi.fn();
    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity
      },
      { idleThresholdMs: 1, idleCooldownMs: 60_000 }
    );
    engine.recordUserActivity("2026-08-19T00:00:00.000Z");

    const activities = await engine.checkTriggers("2026-08-19T00:01:00.000Z");

    expect(activities.map((activity) => activity.kind)).toContain("proactive_idle");
    expect(broadcastPetActivity).toHaveBeenCalledTimes(1);
  });

  it("suppresses an idle nudge before the threshold", async () => {
    const services = createTestServices("aurabot-proactive-");
    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: vi.fn()
      },
      { idleThresholdMs: 60_000 }
    );
    engine.recordUserActivity("2026-08-19T00:00:00.000Z");

    expect(await engine.checkTriggers("2026-08-19T00:00:30.000Z")).toEqual([]);
  });

  it("disables legacy Memory follow-ups while retaining idle nudge", async () => {
    const services = createTestServices("aurabot-proactive-owner-cutover-");
    const retrieveDueFollowUps = vi.spyOn(services.memoryService, "retrieveDueFollowUps");
    const completeFollowUps = vi.spyOn(services.memoryService, "completeFollowUps");
    const engine = new ProactiveTriggerEngine(
      {
        profileService: services.profileService,
        memoryService: services.memoryService,
        eventLogService: services.eventLogService,
        voiceAdapter: services.voiceAdapter,
        broadcastPetActivity: vi.fn()
      },
      {
        memoryFollowUpsEnabled: false,
        idleThresholdMs: 1,
        idleCooldownMs: 60_000
      }
    );
    engine.recordUserActivity("2026-08-19T00:00:00.000Z");

    const activities = await engine.checkTriggers("2026-08-19T00:01:00.000Z");

    expect(activities.map((activity) => activity.kind)).toEqual(["proactive_idle"]);
    expect(retrieveDueFollowUps).not.toHaveBeenCalled();
    expect(completeFollowUps).not.toHaveBeenCalled();
  });
});
