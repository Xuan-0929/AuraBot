import { afterEach, describe, expect, it } from "vitest";
import type { ChatModel } from "../../../src/core/llm/chatModel";
import type { GateModel } from "../../../src/core/llm/gateModel";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";
import { ChatOrchestrator } from "../../../src/core/orchestration/chatOrchestrator";

afterEach(cleanupTestServices);

describe("chat model integration", () => {
  it("uses the injected chat model for normal assistant replies", async () => {
    const services = createTestServices("aurabot-chat-model-");
    const gateModel: GateModel = {
      evaluate: async (input) => ({
        replyDecision: "reply",
        inputKind: "chat",
        memoryDecision: "skip",
        confidence: 0.92,
        reason: "model gate",
        coalescedText: input.currentMessage,
        topicEnded: true
      })
    };
    const chatModel: ChatModel = {
      generateReply: async () => "这是强模型生成的回复。"
    };
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel,
      chatModel
    });

    const result = await orchestrator.sendMessage({ content: "你现在在吗？" });

    expect(result.assistantMessage?.content).toBe("这是强模型生成的回复。");
    expect(result.gateDecision?.reason).toBe("model gate");
  });
});
