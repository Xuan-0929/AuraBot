import type { GateDecision } from "@shared/contracts/chat";
import type { MemoryNode } from "@shared/schemas/memory";
import type { Soul } from "@shared/schemas/profile";
import type { ModelRoleConfig } from "@shared/schemas/config";
import { planAssistantResponse } from "../orchestration/responsePlanner";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { LangChainOpenAIChatCompletionClient } from "./langChainChatModel";
import { ModelBackedChatModel, type ChatModelClient } from "./modelBackedChatModel";
import {
  createMissingModelConfigurationError,
  createUnsupportedModelProviderError,
  isUsableModelRoleConfig,
  shouldUseDeterministicModelTestFallback
} from "./modelErrors";

export interface ChatModelInput {
  userContent: string;
  soul?: Soul;
  writtenMemories: MemoryNode[];
  recalledMemories: MemoryNode[];
  dueFollowUps?: MemoryNode[];
  gateDecision: GateDecision;
}

export interface ChatReplyObserver {
  onDelta?(delta: string): void | Promise<void>;
}

export interface ChatModel {
  generateReply(input: ChatModelInput, observer?: ChatReplyObserver): Promise<string>;
}

export type ChatModelConfig = ModelRoleConfig;

export interface ChatModelFactoryDependencies {
  openAICompatibleClient?: ChatModelClient;
  langChainClient?: ChatModelClient;
}

export class DeterministicChatModel implements ChatModel {
  async generateReply(input: ChatModelInput, observer?: ChatReplyObserver): Promise<string> {
    const reply = planAssistantResponse({
      userContent: input.userContent,
      soul: input.soul,
      writtenMemories: input.writtenMemories,
      recalledMemories: input.recalledMemories,
      dueFollowUps: input.dueFollowUps
    });

    await observer?.onDelta?.(reply);

    return reply;
  }
}

export class UnavailableChatModel implements ChatModel {
  constructor(private readonly error: Error) {}

  async generateReply(): Promise<string> {
    throw this.error;
  }
}

export function createChatModel(config: ChatModelConfig, dependencies: ChatModelFactoryDependencies = {}): ChatModel {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicChatModel();
    }

    return new UnavailableChatModel(createMissingModelConfigurationError("chatModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicChatModel();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedChatModel({
      client: dependencies.langChainClient ?? new LangChainOpenAIChatCompletionClient(),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableChatModel(createUnsupportedModelProviderError("chatModel", config.provider));
  }

  return new ModelBackedChatModel({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}
