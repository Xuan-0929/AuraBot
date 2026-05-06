import { describe, expect, it } from "vitest";
import { createGateModel } from "../../../../src/core/llm/gateModel";
import type { ChatCompletionRequest } from "../../../../src/core/llm/openAiCompatibleClient";

describe("createGateModel", () => {
  it("uses a LangChain structured client when requested by provider", async () => {
    let request: ChatCompletionRequest | undefined;
    const model = createGateModel(
      {
        enabled: true,
        provider: "langchain-openai",
        baseUrl: "https://api.example.com/v1",
        apiKey: "key",
        model: "gate-model",
        temperature: 0,
        timeoutMs: 8000
      },
      {
        langChainClient: {
          complete: async (input) => {
            request = input;

            return JSON.stringify({
              replyDecision: "reply",
              inputKind: "task",
              memoryDecision: "skip",
              confidence: 0.93,
              reason: "user asked Aura to produce an artifact",
              coalescedText: "帮我写一份周报",
              topicEnded: true
            });
          }
        }
      }
    );

    const decision = await model.evaluate({
      currentMessage: "帮我写一份周报",
      recentUserMessages: [],
      recentMessages: []
    });

    expect(decision).toMatchObject({
      replyDecision: "reply",
      inputKind: "task",
      memoryDecision: "skip",
      topicEnded: true,
      reason: "user asked Aura to produce an artifact"
    });
    expect(request).toMatchObject({
      baseUrl: "https://api.example.com/v1",
      apiKey: "key",
      model: "gate-model",
      temperature: 0,
      timeoutMs: 8000
    });
  });
});
