import { afterEach, describe, expect, it } from "vitest";
import type { ChatModel } from "@core/llm/chatModel";
import type { GateModel } from "@core/llm/gateModel";
import { ModelConfigurationError, ModelInvocationError } from "@core/llm/modelErrors";
import type { MemoryExtractor } from "@core/memory/memoryExtractor";
import type { Message } from "@shared/schemas/message";
import { cleanupTestServices, createTestServices } from "../../helpers/testServices";
import { ChatOrchestrator } from "@core/orchestration/chatOrchestrator";

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

  it("returns a recovery message when chatModel is not configured", async () => {
    const services = createTestServices("aurabot-chat-unavailable-");
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
      generateReply: async () => {
        throw new ModelConfigurationError("chatModel", "model API is not configured; missing apiKey.");
      }
    };
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel,
      chatModel
    });

    const result = await orchestrator.sendMessage({ content: "你在吗？" });

    expect(result.assistantMessage?.content).toContain("聊天模型");
    expect(result.assistantMessage?.content).toContain("设置");
    expect(result.assistantMessage?.content).toContain("测试");
    expect(result.agentStatus).toBe("idle");
  });

  it("returns a recovery message when chatModel invocation fails", async () => {
    const services = createTestServices("aurabot-chat-invocation-failed-");
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
      generateReply: async () => {
        throw new ModelInvocationError("chatModel", new Error("OpenAI-compatible request failed with status 401"));
      }
    };
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel,
      chatModel
    });

    const result = await orchestrator.sendMessage({ content: "你在吗？" });

    expect(result.assistantMessage?.content).toContain("聊天模型");
    expect(result.assistantMessage?.content).toContain("API Key");
    expect(result.assistantMessage?.content).toContain("设置");
    expect(result.agentStatus).toBe("idle");
  });

  it("returns a recovery message when memory extraction fails for the current chat turn", async () => {
    const services = createTestServices("aurabot-memory-failed-");
    const gateModel: GateModel = {
      evaluate: async (input) => ({
        replyDecision: "reply",
        inputKind: "chat",
        memoryDecision: "write_now",
        confidence: 0.92,
        reason: "model gate",
        coalescedText: input.currentMessage,
        topicEnded: true
      })
    };
    const chatModel: ChatModel = {
      generateReply: async () => "这条不该出现。"
    };
    const memoryExtractor: MemoryExtractor = {
      extract: async () => {
        throw new ModelConfigurationError("memoryModel", "model API is not configured; missing apiKey.");
      }
    };
    const orchestrator = new ChatOrchestrator({
      ...services,
      gateModel,
      chatModel,
      memoryExtractor
    });

    const result = await orchestrator.sendMessage({ content: "记一下，我喜欢吃火锅。" });

    expect(result.assistantMessage?.content).toContain("记忆模型");
    expect(result.assistantMessage?.content).toContain("设置");
    expect(result.agentStatus).toBe("idle");
  });
});
