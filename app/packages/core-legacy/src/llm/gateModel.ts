import type { GateDecision } from "../../../shared/src/contracts/chat";
import { evaluateGate, type GateEvaluationInput } from "../orchestration/gateEvaluator";
import type { ModelRoleConfig } from "../../../shared/src/schemas/config";
import { OpenAICompatibleChatCompletionClient } from "./openAiCompatibleClient";
import { LangChainStructuredGateClient } from "./langChainGateModel";
import { ModelBackedGateModel, type GateModelClient, structuredGateDecisionSchema } from "./modelBackedGateModel";
import {
  createMissingModelConfigurationError,
  createUnsupportedModelProviderError,
  isUsableModelRoleConfig,
  shouldUseDeterministicModelTestFallback
} from "./modelErrors";

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

export class UnavailableGateModel implements GateModel {
  constructor(private readonly error: Error) {}

  async evaluate(): Promise<GateDecision> {
    throw this.error;
  }
}

export function createGateModel(config: GateModelConfig, dependencies: GateModelFactoryDependencies = {}): GateModel {
  if (!isUsableModelRoleConfig(config)) {
    if (shouldUseDeterministicModelTestFallback()) {
      return new DeterministicGateModel();
    }

    return new UnavailableGateModel(createMissingModelConfigurationError("gateModel", config));
  }

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return new DeterministicGateModel();
  }

  if (config.provider === "langchain-openai") {
    return new ModelBackedGateModel({
      client:
        dependencies.langChainClient ?? new LangChainStructuredGateClient({ schema: structuredGateDecisionSchema }),
      config
    });
  }

  if (config.provider !== "openai-compatible") {
    return new UnavailableGateModel(createUnsupportedModelProviderError("gateModel", config.provider));
  }

  return new ModelBackedGateModel({
    client: dependencies.openAICompatibleClient ?? new OpenAICompatibleChatCompletionClient(),
    config
  });
}
