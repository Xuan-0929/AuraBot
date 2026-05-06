import type { GateDecision } from "../../shared/contracts/chat";
import { evaluateGate, type GateEvaluationInput } from "../orchestration/gateEvaluator";
import type { ModelRoleConfig } from "../../shared/schemas/config";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { LangChainStructuredGateClient } from "./langChainGateModel";
import { ModelBackedGateModel, type GateModelClient, structuredGateDecisionSchema } from "./modelBackedGateModel";

export interface GateModel {
  evaluate(input: GateEvaluationInput): Promise<GateDecision>;
}

export type GateModelConfig = ModelRoleConfig;

export interface GateModelFactoryDependencies {
  openAICompatibleClient?: GateModelClient;
  langChainClient?: GateModelClient;
}

export class DeterministicGateModel implements GateModel {
  async evaluate(input: GateEvaluationInput): Promise<GateDecision> {
    return evaluateGate(input);
  }
}

export function createGateModel(config: GateModelConfig, dependencies: GateModelFactoryDependencies = {}): GateModel {
  if (!config.enabled || !config.baseUrl || !config.apiKey) {
    return new DeterministicGateModel();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedGateModel({
      client:
        dependencies.langChainClient ?? new LangChainStructuredGateClient({ schema: structuredGateDecisionSchema }),
      config,
      fallbackModel: new DeterministicGateModel()
    });
  }

  if (config.provider !== "openai-compatible") {
    return new DeterministicGateModel();
  }

  return new ModelBackedGateModel({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config,
    fallbackModel: new DeterministicGateModel()
  });
}
