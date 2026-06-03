import { describe, expect, it } from "vitest";
import { OpenAICompatibleChatCompletionClient } from "@core/llm/openAiCompatibleClient";

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

  it("streams chat completion deltas from an OpenAI-compatible SSE response", async () => {
    const requests: Array<{ url: string; init: RequestInit }> = [];
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode(
            [
              'data: {"choices":[{"delta":{"content":"你"}}]}',
              'data: {"choices":[{"delta":{"content":"好呀"}}]}',
              "data: [DONE]",
              ""
            ].join("\n\n")
          )
        );
        controller.close();
      }
    });
    const client = new OpenAICompatibleChatCompletionClient({
      fetchImplementation: async (url, init) => {
        requests.push({ url: String(url), init: init ?? {} });

        return new Response(stream, {
          status: 200,
          headers: {
            "content-type": "text/event-stream"
          }
        });
      }
    });
    const deltas: string[] = [];

    const content = await client.streamComplete(
      {
        baseUrl: "https://api.example.com/v1/",
        apiKey: "test-key",
        model: "strong-chat-model",
        messages: [
          {
            role: "user",
            content: "你好"
          }
        ]
      },
      (delta) => {
        deltas.push(delta);
      }
    );

    expect(content).toBe("你好呀");
    expect(deltas).toEqual(["你", "好呀"]);
    expect(JSON.parse(String(requests[0]?.init.body))).toMatchObject({
      stream: true
    });
  });
});
