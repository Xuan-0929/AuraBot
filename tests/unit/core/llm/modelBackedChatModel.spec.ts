import { describe, expect, it } from "vitest";
import { ModelBackedChatModel } from "../../../../src/core/llm/modelBackedChatModel";
import type { ChatCompletionRequest } from "../../../../src/core/llm/openAiCompatibleClient";
import type { Soul } from "../../../../src/shared/schemas/profile";

const companionSoul: Soul = {
  id: "soul_default",
  profileId: "profile_default",
  name: "Aura",
  persona: "伴侣兼助手，亲近、可靠，会自然陪用户推进事情。",
  tone: "短句，自然，有一点亲密感。",
  principles: ["任务完成时像本人自然告诉用户结果。"],
  taboos: ["不要用客服工单或系统播报的语义口吻。"],
  speakingStyle: "短句，自然，有一点亲密感，不要像客服或机器人。",
  relationship: "长期陪伴用户的桌面伴侣和助手。",
  boundaries: ["不要说自己是 AI、模型或机器人。"],
  topicPreferences: ["daily life", "projects"],
  createdAt: "2026-05-05T00:00:00.000Z",
  updatedAt: "2026-05-05T00:00:00.000Z"
};

describe("ModelBackedChatModel", () => {
  it("injects the soul into the chat prompt and forbids AI self-description", async () => {
    let request: ChatCompletionRequest | undefined;
    const model = new ModelBackedChatModel({
      client: {
        complete: async (input) => {
          request = input;

          return "好，我在。";
        }
      },
      config: {
        enabled: true,
        provider: "openai-compatible",
        baseUrl: "https://example.test/v1",
        apiKey: "key",
        model: "chat-model"
      }
    });

    await model.generateReply({
      userContent: "你是谁？",
      soul: companionSoul,
      writtenMemories: [],
      recalledMemories: [],
      dueFollowUps: [],
      gateDecision: {
        replyDecision: "reply",
        inputKind: "chat",
        memoryDecision: "skip",
        confidence: 0.8,
        reason: "test",
        coalescedText: "你是谁？",
        topicEnded: true
      }
    });

    expect(request?.messages[0]?.content).toContain("伴侣兼助手");
    expect(request?.messages[0]?.content).toContain("不要说自己是 AI、模型或机器人");
    expect(request?.messages[0]?.content).toContain("不要像客服或机器人");
    expect(request?.messages[1]?.content).toContain("长期陪伴用户的桌面伴侣和助手");
  });
});
