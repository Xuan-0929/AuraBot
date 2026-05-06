import { describe, expect, it } from "vitest";
import { createChatModel } from "../../../../src/core/llm/chatModel";
import type { ChatCompletionRequest } from "../../../../src/core/llm/openAiCompatibleClient";

describe("createChatModel", () => {
  it("uses a LangChain OpenAI-compatible client when requested by provider", async () => {
    let request: ChatCompletionRequest | undefined;
    const model = createChatModel(
      {
        enabled: true,
        provider: "langchain-openai",
        baseUrl: "https://api.example.com/v1",
        apiKey: "key",
        model: "chat-model",
        temperature: 0.6
      },
      {
        langChainClient: {
          complete: async (input) => {
            request = input;

            return "LangChain reply";
          }
        }
      }
    );

    const reply = await model.generateReply({
      userContent: "hello",
      writtenMemories: [],
      recalledMemories: [],
      dueFollowUps: [],
      gateDecision: {
        replyDecision: "reply",
        inputKind: "chat",
        memoryDecision: "skip",
        confidence: 0.9,
        reason: "test",
        coalescedText: "hello",
        topicEnded: true
      }
    });

    expect(reply).toBe("LangChain reply");
    expect(request).toMatchObject({
      baseUrl: "https://api.example.com/v1",
      apiKey: "key",
      model: "chat-model",
      temperature: 0.6
    });
  });
});
