import { describe, expect, it } from "vitest";
import { OpenAICompatibleChatCompletionClient } from "../../../../src/core/llm/openAiCompatibleClient";

describe("OpenAICompatibleChatCompletionClient", () => {
  it("posts chat completions to an OpenAI-compatible endpoint", async () => {
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const client = new OpenAICompatibleChatCompletionClient({
      fetchImplementation: async (url, init) => {
        requests.push({ url: String(url), init: init ?? {} });

        return new Response(
          JSON.stringify({
            choices: [
              {
                message: {
                  content: "外部模型回复"
                }
              }
            ]
          }),
          {
            status: 200,
            headers: {
              "content-type": "application/json"
            }
          }
        );
      }
    });

    const content = await client.complete({
      baseUrl: "https://api.example.com/v1/",
      apiKey: "test-key",
      model: "strong-chat-model",
      temperature: 0.4,
      messages: [
        {
          role: "user",
          content: "你好"
        }
      ]
    });

    expect(content).toBe("外部模型回复");
    expect(requests).toHaveLength(1);
    expect(requests[0]?.url).toBe("https://api.example.com/v1/chat/completions");
    expect(requests[0]?.init.method).toBe("POST");
    expect((requests[0]?.init.headers as Record<string, string>).Authorization).toBe("Bearer test-key");
    expect(JSON.parse(String(requests[0]?.init.body))).toMatchObject({
      model: "strong-chat-model",
      temperature: 0.4
    });
  });
});
