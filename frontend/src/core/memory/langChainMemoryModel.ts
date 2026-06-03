import { ChatOpenAI } from "@langchain/openai";
import type { z } from "zod";
import type { ChatCompletionMessage, ChatCompletionRequest } from "../llm/openAiCompatibleClient";
import type { LangChainOpenAIChatModelOptions } from "../llm/langChainChatModel";

export interface LangChainStructuredRunnable {
  invoke(messages: ChatCompletionMessage[]): Promise<unknown>;
}

export interface LangChainStructuredChatModelLike {
  withStructuredOutput(schema: z.ZodType): LangChainStructuredRunnable;
}

export interface LangChainStructuredMemoryClientOptions {
  schema: z.ZodType;
  chatModelFactory?: (options: LangChainOpenAIChatModelOptions) => LangChainStructuredChatModelLike;
}

export class LangChainStructuredMemoryClient {
  private readonly chatModelFactory: (options: LangChainOpenAIChatModelOptions) => LangChainStructuredChatModelLike;

  constructor(private readonly options: LangChainStructuredMemoryClientOptions) {
    this.chatModelFactory = options.chatModelFactory ?? createDefaultChatModel;
  }

  async complete(input: ChatCompletionRequest): Promise<string> {
    const model = this.chatModelFactory(toLangChainOptions(input));
    const structured = model.withStructuredOutput(this.options.schema);
    const result = await structured.invoke(input.messages);

    return JSON.stringify(result);
  }
}

function createDefaultChatModel(options: LangChainOpenAIChatModelOptions): LangChainStructuredChatModelLike {
  return new ChatOpenAI(options) as unknown as LangChainStructuredChatModelLike;
}

function toLangChainOptions(input: ChatCompletionRequest): LangChainOpenAIChatModelOptions {
  return {
    model: input.model,
    temperature: input.temperature,
    apiKey: input.apiKey,
    timeout: input.timeoutMs,
    configuration: {
      baseURL: input.baseUrl,
      apiKey: input.apiKey
    },
    streamUsage: false
  };
}
