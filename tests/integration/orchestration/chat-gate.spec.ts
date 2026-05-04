import { afterEach, describe, expect, it } from "vitest";
import { cleanupTestServices, createTestOrchestrator } from "../../helpers/testServices";

afterEach(cleanupTestServices);

describe("chat gate integration", () => {
  it("persists fragmented user input but waits instead of replying or writing memory", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-gate-");

    const result = await orchestrator.sendMessage({ content: "然后我觉得" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    const messages = services.messageRepository.listBySession(mainSession.id);
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const eventLogs = services.eventLogService.list(20);

    expect(result.assistantMessage).toBeUndefined();
    expect(result.agentStatus).toBe("listening");
    expect(messages).toHaveLength(1);
    expect(memoryContext.nodes).toHaveLength(0);
    expect(eventLogs.some((log) => log.eventType === "gate.decision" && log.payload.replyDecision === "wait")).toBe(
      true
    );
  });

  it("coalesces recent fragments when a later message completes the thought", async () => {
    const { services, orchestrator } = createTestOrchestrator("aurabot-gate-");

    await orchestrator.sendMessage({ content: "我昨天想了想" });
    const result = await orchestrator.sendMessage({ content: "我喜欢吃火锅。" });
    const profile = services.profileService.getOrSeedActiveProfile();
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);

    expect(result.assistantMessage?.content).toContain("火锅");
    expect(memoryContext.nodes.some((node) => node.content.includes("火锅"))).toBe(true);
  });
});
