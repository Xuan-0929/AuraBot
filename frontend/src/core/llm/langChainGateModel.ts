import { ChatOpenAI } from "@langchain/openai";
import type { z } from "zod";
import type { ChatCompletionMessage, ChatCompletionRequest } from "./openAiCompatibleClient";
import type { LangChainOpenAIChatModelOptions } from "./langChainChatModel";

export interface LangChainStructuredGateRunnable {
  invoke(messages: ChatCompletionMessage[]): Promise<unknown>;
}

export interface LangChainStructuredGateModelLike {
  withStructuredOutput(schema: z.ZodType): LangChainStructuredGateRunnable;
}

export interface LangChainStructuredGateClientOptions {
  schema: z.ZodType;
  chatModelFactory?: (options: LangChainOpenAIChatModelOptions) => LangChainStructuredGateModelLike;
}

export class LangChainStructuredGateClient {
  private readonly chatModelFactory: (options: LangChainOpenAIChatModelOptions) => LangChainStructuredGateModelLike;

  constructor(private readonly options: LangChainStructuredGateClientOptions) {
    this.chatModelFactory = options.chatModelFactory ?? createDefaultChatModel;
  }

  async complete(input: ChatCompletionRequest): Promise<string> {
    const model = this.chatModelFactory(toLangChainOptions(input));
    const structured = model.withStructuredOutput(this.options.schema);
    const result = await structured.invoke(input.messages);

    return JSON.stringify(result);
  }
}

function createDefaultChatModel(options: LangChainOpenAIChatModelOptions): LangChainStructuredGateModelLike {
  return new ChatOpenAI(options) as unknown as LangChainStructuredGateModelLike;
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
