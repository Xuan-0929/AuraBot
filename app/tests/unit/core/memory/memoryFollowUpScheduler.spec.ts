import { afterEach, describe, expect, it } from "vitest";
import type { PetActivity } from "../../../../packages/shared/src/contracts/pet";
import { MemoryFollowUpScheduler } from "../../../../packages/core-legacy/src/memory/memoryFollowUpScheduler";
import { cleanupTestServices, createTestServices } from "../../../helpers/testServices";

afterEach(cleanupTestServices);

describe("MemoryFollowUpScheduler", () => {
  it("broadcasts due follow-up memories through the pet activity channel and completes them", async () => {
    const services = createTestServices("aurabot-memory-follow-up-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const activities: PetActivity[] = [];

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

    const scheduler = new MemoryFollowUpScheduler({
      profileService: services.profileService,
      memoryService: services.memoryService,
      eventLogService: services.eventLogService,
      voiceAdapter: services.voiceAdapter,
      broadcastPetActivity: (activity) => activities.push(activity)
    });

    const result = await scheduler.checkDueFollowUps("2026-05-05T20:01:00.000+08:00");
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const followUp = memoryContext.nodes.find((node) => node.type === "follow_up");
    const eventLogs = services.eventLogService.list(20);

    expect(result).toHaveLength(1);
    expect(activities).toHaveLength(1);
    expect(activities[0]).toMatchObject({
      kind: "memory_follow_up",
      message: expect.stringContaining("算法面试")
    });
    expect(followUp?.state).toBe("completed");
    expect(eventLogs).toContainEqual(
      expect.objectContaining({
        eventType: "memory.follow_up_triggered",
        payload: expect.objectContaining({
          profileId: profile.id,
          trigger: "scheduler",
          nodeIds: [followUp?.id]
        })
      })
    );
  });
});
