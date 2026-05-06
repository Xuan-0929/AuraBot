import type { GateDecision } from "../../shared/contracts/chat";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { Soul } from "../../shared/schemas/profile";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import { planAssistantResponse } from "../orchestration/responsePlanner";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { LangChainOpenAIChatCompletionClient } from "./langChainChatModel";
import { ModelBackedChatModel, type ChatModelClient } from "./modelBackedChatModel";

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

export function createChatModel(config: ChatModelConfig, dependencies: ChatModelFactoryDependencies = {}): ChatModel {
  if (!config.enabled || !config.baseUrl || !config.apiKey) {
    return new DeterministicChatModel();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedChatModel({
      client: dependencies.langChainClient ?? new LangChainOpenAIChatCompletionClient(),
      config,
      fallbackModel: new DeterministicChatModel()
    });
  }

  if (config.provider !== "openai-compatible") {
    return new DeterministicChatModel();
  }

  return new ModelBackedChatModel({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config,
    fallbackModel: new DeterministicChatModel()
  });
}
