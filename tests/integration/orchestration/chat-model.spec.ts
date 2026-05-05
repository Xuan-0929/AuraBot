import { afterEach, describe, expect, it } from "vitest";
import type { ChatModel } from "../../../src/core/llm/chatModel";
import type { GateModel } from "../../../src/core/llm/gateModel";
import type { Message } from "../../../src/shared/schemas/message";
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

  it("streams normal assistant replies into one persisted assistant message", async () => {
    const services = createTestServices("aurabot-chat-stream-");
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
      generateReply: async (_input, observer) => {
        observer?.onDelta?.("这是");
        observer?.onDelta?.("流式");

        return "这是流式回复。";
      }
    };
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel,
      chatModel
    });
    const createdMessages: Message[] = [];
    const updatedMessages: Message[] = [];

    const result = await orchestrator.sendMessage(
      { content: "你现在在吗？" },
      {
        onAssistantMessageCreated: (message) => {
          createdMessages.push(message);
        },
        onAssistantMessageUpdated: (message) => {
          updatedMessages.push(message);
        }
      }
    );

    expect(createdMessages).toHaveLength(1);
    expect(createdMessages[0]?.content).toBe("");
    expect(updatedMessages.map((message) => message.content)).toEqual([
      "这是",
      "这是流式",
      "这是流式回复。"
    ]);
    expect(result.assistantMessage?.content).toBe("这是流式回复。");
    expect(new Set(updatedMessages.map((message) => message.id))).toEqual(new Set([createdMessages[0]?.id]));
  });
});
