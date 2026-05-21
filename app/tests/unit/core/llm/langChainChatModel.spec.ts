import { describe, expect, it } from "vitest";
import {
  LangChainOpenAIChatCompletionClient,
  type LangChainOpenAIChatModelOptions
} from "../../../../packages/core-legacy/src/llm/langChainChatModel";
import type { ChatCompletionRequest } from "../../../../packages/core-legacy/src/llm/openAiCompatibleClient";

const request: ChatCompletionRequest = {
  baseUrl: "https://api.example.com/v1",
  apiKey: "test-key",
  model: "chat-model",
  temperature: 0.4,
  timeoutMs: 12000,
  messages: [
    {
      role: "system",
      content: "You are Aura."
    },
    {
      role: "user",
      content: "Hi"
    }
  ]
};

describe("LangChainOpenAIChatCompletionClient", () => {
  it("invokes a LangChain chat model with AuraBot chat completion settings", async () => {
    let modelOptions: LangChainOpenAIChatModelOptions | undefined;
    let messages: unknown;
    const client = new LangChainOpenAIChatCompletionClient({
      chatModelFactory: (options) => {
        modelOptions = options;

        return {
          async invoke(inputMessages) {
            messages = inputMessages;

            return {
              content: "好，我在。"
            };
          }
        };
      }
    });

    const reply = await client.complete(request);

    expect(reply).toBe("好，我在。");
    expect(modelOptions).toMatchObject({
      model: "chat-model",
      temperature: 0.4,
      apiKey: "test-key",
      timeout: 12000,
      configuration: {
        baseURL: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });
    expect(messages).toEqual(request.messages);
  });

  it("streams LangChain chat chunks through the existing delta observer", async () => {
    const client = new LangChainOpenAIChatCompletionClient({
      chatModelFactory: () => ({
        async invoke() {
          throw new Error("stream should be used");
        },
        async *stream() {
          yield { content: "你好" };
          yield { content: [{ type: "text", text: "，我在。" }] };
        }
      })
    });
    const deltas: string[] = [];

    const reply = await client.streamComplete(request, (delta) => {
      deltas.push(delta);
    });

    expect(reply).toBe("你好，我在。");
    expect(deltas).toEqual(["你好", "，我在。"]);
  });
});
