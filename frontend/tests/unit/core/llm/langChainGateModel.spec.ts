import { z } from "zod";
import { describe, expect, it } from "vitest";
import { LangChainStructuredGateClient } from "@core/llm/langChainGateModel";
import type { ChatCompletionRequest } from "@core/llm/openAiCompatibleClient";

const request: ChatCompletionRequest = {
  baseUrl: "https://api.example.com/v1",
  apiKey: "test-key",
  model: "gate-model",
  temperature: 0,
  timeoutMs: 8000,
  messages: [
    {
      role: "system",
      content: "Return structured gate JSON."
    },
    {
      role: "user",
      content: "{\"currentMessage\":\"帮我写周报\"}"
    }
  ]
};

describe("LangChainStructuredGateClient", () => {
  it("uses LangChain structured output with the provided gate schema", async () => {
    const schema = z.object({
      replyDecision: z.literal("reply"),
      inputKind: z.literal("task"),
      memoryDecision: z.literal("skip"),
      confidence: z.number(),
      reason: z.string(),
      coalescedText: z.string(),
      topicEnded: z.boolean()
    });
    let modelOptions: Record<string, unknown> | undefined;
    let structuredSchema: unknown;
    let invokedMessages: unknown;
    const client = new LangChainStructuredGateClient({
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
                  replyDecision: "reply",
                  inputKind: "task",
                  memoryDecision: "skip",
                  confidence: 0.91,
                  reason: "artifact request",
                  coalescedText: "帮我写周报",
                  topicEnded: true
                };
              }
            };
          }
        };
      }
    });

    const content = await client.complete(request);

    expect(JSON.parse(content)).toEqual({
      replyDecision: "reply",
      inputKind: "task",
      memoryDecision: "skip",
      confidence: 0.91,
      reason: "artifact request",
      coalescedText: "帮我写周报",
      topicEnded: true
    });
    expect(modelOptions).toMatchObject({
      model: "gate-model",
      temperature: 0,
      apiKey: "test-key",
      timeout: 8000,
      configuration: {
        baseURL: "https://api.example.com/v1",
        apiKey: "test-key"
      }
    });
    expect(structuredSchema).toBe(schema);
    expect(invokedMessages).toEqual(request.messages);
  });
});
