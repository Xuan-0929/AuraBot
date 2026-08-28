/**
 * Adapts AuraBot's legacy Console ChatModel boundary to an OpenAI-compatible client.
 * It builds the Console prompt from active Profile conversation/memory facts and the shared
 * `buildSoulPrompt`, keeping Electron's structured Soul as the only personality source used by both
 * Console chat and the Main Agent realtime token request.
 */

import type { GateDecision } from "@shared/contracts/chat";
import type { MemoryNode } from "@shared/schemas/memory";
import type { ModelRoleConfig } from "@shared/schemas/config";
import type { ChatCompletionRequest } from "./openAiCompatibleClient";
import type { ChatModel, ChatModelInput, ChatReplyObserver } from "./chatModel";
import { ModelInvocationError } from "./modelErrors";
import { buildSoulPrompt } from "../profiles/soulPrompt";

export interface ChatModelClient {
  complete(input: ChatCompletionRequest): Promise<string>;
  streamComplete?(input: ChatCompletionRequest, onDelta: (delta: string) => void | Promise<void>): Promise<string>;
}

export interface ModelBackedChatModelOptions {
  client: ChatModelClient;
  config?: ModelRoleConfig;
}

export class ModelBackedChatModel implements ChatModel {
  constructor(private readonly options: ModelBackedChatModelOptions) {}

  async generateReply(input: ChatModelInput, observer?: ChatReplyObserver): Promise<string> {
    const request: ChatCompletionRequest = {
      baseUrl: this.options.config?.baseUrl ?? "",
      apiKey: this.options.config?.apiKey ?? "",
      model: this.options.config?.model ?? "chat-model",
      temperature: this.options.config?.temperature ?? 0.7,
      timeoutMs: this.options.config?.timeoutMs,
      messages: buildChatPrompt(input)
    };

    try {
      if (this.options.client.streamComplete) {
        return await this.options.client.streamComplete(request, async (delta) => {
          await observer?.onDelta?.(delta);
        });
      }

      const reply = await this.options.client.complete(request);

      await observer?.onDelta?.(reply);

      return reply;
    } catch (error) {
      throw new ModelInvocationError("chatModel", error);
    }
  }
}

function buildChatPrompt(input: ChatModelInput): ChatCompletionRequest["messages"] {
  return [
    {
      role: "system",
      content: buildSoulPrompt(input.soul)
    },
    {
      role: "user",
      content: JSON.stringify({
        userContent: input.userContent,
        soul: input.soul,
        writtenMemories: summarizeMemories(input.writtenMemories),
        recalledMemories: summarizeMemories(input.recalledMemories),
        dueFollowUps: summarizeMemories(input.dueFollowUps ?? []),
        gateDecision: input.gateDecision
      })
    }
  ];
}

function summarizeMemories(memories: MemoryNode[]): Array<{ type: string; content: string }> {
  return memories.map((memory) => ({
    type: memory.type,
    content: memory.content
  }));
}
