import { z } from "zod";
import { describe, expect, it } from "vitest";
import { LangChainStructuredMemoryClient } from "../../../../packages/core-legacy/src/memory/langChainMemoryModel";
import type { ChatCompletionRequest } from "../../../../packages/core-legacy/src/llm/openAiCompatibleClient";

const request: ChatCompletionRequest = {
  baseUrl: "https://api.example.com/v1",
  apiKey: "test-key",
  model: "memory-model",
  temperature: 0,
  timeoutMs: 12000,
  messages: [
    {
      role: "system",
      content: "Return structured memory JSON."
    },
    {
      role: "user",
      content: "{\"message\":\"我喜欢火锅\"}"
    }
  ]
};

describe("LangChainStructuredMemoryClient", () => {
  it("uses LangChain structured output with the provided schema", async () => {
    const schema = z.object({
      candidates: z.array(
        z.object({
          type: z.literal("preference"),
          content: z.string()
        })
      )
    });
    let modelOptions: Record<string, unknown> | undefined;
    let structuredSchema: unknown;
    let invokedMessages: unknown;
    const client = new LangChainStructuredMemoryClient({
      schema,
      chatModelFactory: (options) => {
        modelOptions = options as unknown as Record<string, unknown>;

        return {
          withStructuredOutput(inputSchema) {
            structuredSchema = inputSchema;

            return {
              async invoke(messages) {
                invokedMessages = messages;

                return {
                  candidates: [
                    {
                      type: "preference",
                      content: "用户喜欢火锅"
                    }
                  ]
                };
              }
            };
          }
        };
      }
    });

    const content = await client.complete(request);

    expect(JSON.parse(content)).toEqual({
      candidates: [
        {
          type: "preference",
          content: "用户喜欢火锅"
        }
      ]
    });
    expect(modelOptions).toMatchObject({
      model: "memory-model",
      temperature: 0,
      apiKey: "test-key",
      timeout: 12000,
      configuration: {
        baseURL: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });
    expect(structuredSchema).toBe(schema);
    expect(invokedMessages).toEqual(request.messages);
  });
});
