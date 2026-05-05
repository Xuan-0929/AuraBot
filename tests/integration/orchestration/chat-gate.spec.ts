import { afterEach, describe, expect, it } from "vitest";
import type { GateDecision } from "../../../src/shared/contracts/chat";
import type { GateEvaluationInput } from "../../../src/core/orchestration/gateEvaluator";
import { ChatOrchestrator } from "../../../src/core/orchestration/chatOrchestrator";
import { cleanupTestServices, createTestOrchestrator, createTestServices } from "../../helpers/testServices";

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

  it.each(["在干嘛", "说两句"])("replies to short direct social prompt %s", async (content) => {
    const { orchestrator } = createTestOrchestrator("aurabot-gate-social-");

    const result = await orchestrator.sendMessage({ content });

    expect(result.agentStatus).toBe("idle");
    expect(result.assistantMessage?.content).toBeTruthy();
    expect(result.gateDecision).toMatchObject({
      replyDecision: "reply",
      inputKind: "chat"
    });
  });

  it("does not force short non-fragment chat to keep listening by length alone", async () => {
    const { orchestrator } = createTestOrchestrator("aurabot-gate-short-chat-");

    const result = await orchestrator.sendMessage({ content: "随便聊聊" });

    expect(result.agentStatus).toBe("idle");
    expect(result.assistantMessage?.content).toBeTruthy();
    expect(result.gateDecision?.reason).not.toContain("fragment");
  });

  it("opens recent full conversation context to the gate model", async () => {
    const services = createTestServices("aurabot-gate-context-");
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
    let capturedInput: GateEvaluationInput | undefined;

    for (let index = 1; index <= 6; index += 1) {
      services.messageRepository.create({
        sessionId: mainSession.id,
        profileId: profile.id,
        role: "user",
        content: `用户上下文 ${index}`
      });
      services.messageRepository.create({
        sessionId: mainSession.id,
        profileId: profile.id,
        role: "assistant",
        content: `Aura 上下文 ${index}`
      });
    }

    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel: {
        evaluate: async (input) => {
          capturedInput = input;

          return {
            replyDecision: "wait",
            inputKind: "chat",
            memoryDecision: "defer",
            confidence: 0.7,
            reason: "test",
            coalescedText: input.currentMessage,
            topicEnded: false
          } satisfies GateDecision;
        }
      }
    });

    await orchestrator.sendMessage({ content: "那我接着说" });

    const recentMessages = capturedInput?.recentMessages;
    expect(recentMessages).toBeDefined();
    expect(recentMessages).toHaveLength(10);
    expect(recentMessages?.some((message) => message.role === "assistant")).toBe(true);
    expect(recentMessages?.at(-1)).toMatchObject({
      role: "assistant",
      content: "Aura 上下文 6"
    });
    expect(capturedInput?.recentUserMessages).toEqual([
      "用户上下文 2",
      "用户上下文 3",
      "用户上下文 4",
      "用户上下文 5",
      "用户上下文 6"
    ]);
  });
});
