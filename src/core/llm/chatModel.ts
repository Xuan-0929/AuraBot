import type { GateDecision } from "../../shared/contracts/chat";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { Soul } from "../../shared/schemas/profile";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import { planAssistantResponse } from "../orchestration/responsePlanner";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { ModelBackedChatModel } from "./modelBackedChatModel";

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

export function createChatModel(config: ChatModelConfig): ChatModel {
  if (!config.enabled || config.provider !== "openai-compatible" || !config.baseUrl || !config.apiKey) {
    return new DeterministicChatModel();
  }

  return new ModelBackedChatModel({
    client: new OpenAICompatibleChatCompletionClient(),
    config,
    fallbackModel: new DeterministicChatModel()
  });
}
