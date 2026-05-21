import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestOrchestrator } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("memory roundtrip", () => {
  it("writes stable preference memory from chat and recalls it later", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-memory-");

    await orchestrator.sendMessage({ content: "我喜欢吃火锅，讨厌跑步。" });
    const recall = await orchestrator.sendMessage({ content: "我喜欢吃什么来着？" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const eventLogs = services.eventLogService.list(20);

    expect(memoryContext.nodes.map((node) => node.type)).toContain("preference");
    expect(memoryContext.nodes.some((node) => node.content.includes("火锅"))).toBe(true);
    expect(recall.assistantMessage?.content).toContain("火锅");
    expect(eventLogs.some((log) => log.eventType === "memory.write_completed")).toBe(true);
    expect(eventLogs.some((log) => log.eventType === "memory.recall_used")).toBe(true);
  });

  it("writes time-driven event memory and recalls it in a follow-up question", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-memory-");

    await orchestrator.sendMessage({ content: "我下周五要去看周杰伦演唱会，提醒我别忘了。" });
    const recall = await orchestrator.sendMessage({ content: "我下周有什么事来着？" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);

    expect(memoryContext.nodes.some((node) => node.type === "event" && node.content.includes("演唱会"))).toBe(
      true
    );
    expect(memoryContext.nodes.some((node) => node.type === "follow_up" && node.content.includes("提醒"))).toBe(
      true
    );
    expect(recall.assistantMessage?.content).toContain("演唱会");
  });

  it("links related memories so recall can jump from an event to a stable preference", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-memory-");

    await orchestrator.sendMessage({ content: "我喜欢吃火锅。" });
    await orchestrator.sendMessage({ content: "我明天要和阿杰去吃火锅，提醒我别忘了。" });
    const recall = await orchestrator.sendMessage({ content: "阿杰 那件事我该顺便注意什么？" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const preference = memoryContext.nodes.find((node) => node.type === "preference" && node.content.includes("火锅"));
    const event = memoryContext.nodes.find((node) => node.type === "event" && node.content.includes("阿杰"));

    expect(preference).toBeDefined();
    expect(event).toBeDefined();
    expect(
      memoryContext.edges.some(
        (edge) =>
          edge.relationType === "preference_for" &&
          edge.sourceNodeId === event?.id &&
          edge.targetNodeId === preference?.id
      )
    ).toBe(true);
    expect(recall.assistantMessage?.content).toContain("阿杰");
    expect(recall.assistantMessage?.content).toContain("喜欢火锅");
  });

  it("surfaces due follow-up memories in the next main chat reply and completes them", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-memory-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);

    await services.memoryService.enqueueWrite({
      profileId: profile.id,
      sourceSessionId: mainSession.id,
      node: {
        type: "follow_up",
        content: "提醒用户准备今晚的算法面试",
        importance: 0.88,
        startsAt: "2026-05-04T20:00:00.000+08:00"
      },
      edges: []
    });

    const result = await orchestrator.sendMessage({ content: "你在吗？" });
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const followUp = memoryContext.nodes.find((node) => node.type === "follow_up");
    const eventLogs = services.eventLogService.list(20);

    expect(result.assistantMessage?.content).toContain("提醒");
    expect(result.assistantMessage?.content).toContain("算法面试");
    expect(followUp?.state).toBe("completed");
    expect(eventLogs.some((log) => log.eventType === "memory.follow_up_triggered")).toBe(true);
  });
});
