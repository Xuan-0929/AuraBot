import type { GateDecision } from "../../shared/contracts/chat";
import type { MemoryNode } from "../../shared/schemas/memory";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import { planAssistantResponse } from "../orchestration/responsePlanner";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { ModelBackedChatModel } from "./modelBackedChatModel";

export interface ChatModelInput {
  userContent: string;
  writtenMemories: MemoryNode[];
  recalledMemories: MemoryNode[];
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
      writtenMemories: input.writtenMemories,
      recalledMemories: input.recalledMemories
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
