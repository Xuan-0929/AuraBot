import type { GateDecision } from "../../shared/contracts/chat";
import { evaluateGate, type GateEvaluationInput } from "../orchestration/gateEvaluator";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { ModelBackedGateModel } from "./modelBackedGateModel";

export interface GateModel {
  evaluate(input: GateEvaluationInput): Promise<GateDecision>;
}

export type GateModelConfig = ModelRoleConfig;

export class DeterministicGateModel implements GateModel {
  async evaluate(input: GateEvaluationInput): Promise<GateDecision> {
    return evaluateGate(input);
  }
}

export function createGateModel(config: GateModelConfig): GateModel {
  if (!config.enabled || config.provider !== "openai-compatible" || !config.baseUrl || !config.apiKey) {
    return new DeterministicGateModel();
  }

  return new ModelBackedGateModel({
    client: new OpenAICompatibleChatCompletionClient(),
    config,
    fallbackModel: new DeterministicGateModel()
  });
}
