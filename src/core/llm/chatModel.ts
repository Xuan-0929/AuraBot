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

export interface ChatModel {
  generateReply(input: ChatModelInput): Promise<string>;
}

export type ChatModelConfig = ModelRoleConfig;

export class DeterministicChatModel implements ChatModel {
  async generateReply(input: ChatModelInput): Promise<string> {
    return planAssistantResponse({
      userContent: input.userContent,
      soul: input.soul,
      writtenMemories: input.writtenMemories,
      recalledMemories: input.recalledMemories,
      dueFollowUps: input.dueFollowUps
    });
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
