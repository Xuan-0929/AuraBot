import { ChatOpenAI } from "@langchain/openai";
import type { ChatCompletionMessage, ChatCompletionRequest } from "./openAiCompatibleClient";

export interface LangChainOpenAIChatModelOptions {
  model: string;
  temperature?: number;
  apiKey: string;
  timeout?: number;
  configuration: {
    baseURL: string;
    apiKey: string;
  };
  streamUsage: boolean;
}

export interface LangChainChatModelLike {
  invoke(messages: ChatCompletionMessage[]): Promise<unknown>;
  stream?(messages: ChatCompletionMessage[]): Promise<AsyncIterable<unknown>> | AsyncIterable<unknown>;
}

export interface LangChainOpenAIChatCompletionClientOptions {
  chatModelFactory?: (options: LangChainOpenAIChatModelOptions) => LangChainChatModelLike;
}

export class LangChainOpenAIChatCompletionClient {
  private readonly chatModelFactory: (options: LangChainOpenAIChatModelOptions) => LangChainChatModelLike;

  constructor(options: LangChainOpenAIChatCompletionClientOptions = {}) {
    this.chatModelFactory = options.chatModelFactory ?? createDefaultChatModel;
  }

  async complete(input: ChatCompletionRequest): Promise<string> {
    const model = this.chatModelFactory(toLangChainOptions(input));
    const result = await model.invoke(input.messages);
    const content = extractTextContent(result);

    if (!content.trim()) {
      throw new Error("LangChain response did not include content");
    }

    return content;
  }

  async streamComplete(
    input: ChatCompletionRequest,
    onDelta: (delta: string) => void | Promise<void>
  ): Promise<string> {
    const model = this.chatModelFactory(toLangChainOptions(input));

    if (!model.stream) {
      const content = await this.complete(input);
      await onDelta(content);

      return content;
    }

    const stream = await model.stream(input.messages);
    let content = "";

    for await (const chunk of stream) {
      const delta = extractTextContent(chunk);

      if (!delta) {
        continue;
      }

      content += delta;
      await onDelta(delta);
    }

    if (!content.trim()) {
      throw new Error("LangChain streaming response did not include content");
    }

    return content;
  }
}

function createDefaultChatModel(options: LangChainOpenAIChatModelOptions): LangChainChatModelLike {
  return new ChatOpenAI(options) as unknown as LangChainChatModelLike;
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

function extractTextContent(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }

  if (!value || typeof value !== "object") {
    return "";
  }

  const content = (value as { content?: unknown }).content;

  if (typeof content === "string") {
    return content;
  }

  if (Array.isArray(content)) {
    return content.map(extractContentPart).join("");
  }

  return "";
}

function extractContentPart(part: unknown): string {
  if (typeof part === "string") {
    return part;
  }

  if (!part || typeof part !== "object") {
    return "";
  }

  const maybeText = (part as { text?: unknown }).text;

  return typeof maybeText === "string" ? maybeText : "";
}
