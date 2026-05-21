import { z } from "zod";
import type {
  ModelConnectionTestInput,
  ModelRoleKey,
  PublicModelHealth,
  PublicModelHealthMap
} from "../../../shared/src/contracts/settings";
import type { AppConfig, ModelRoleConfig } from "../../../shared/src/schemas/config";
import { LangChainStructuredMemoryClient } from "../memory/langChainMemoryModel";
import { LangChainStructuredGateClient } from "./langChainGateModel";
import { LangChainOpenAIChatCompletionClient } from "./langChainChatModel";
import { OpenAICompatibleChatCompletionClient, type ChatCompletionRequest } from "./openAiCompatibleClient";
import {
  createMissingModelConfigurationError,
  createUnsupportedModelProviderError,
  isUsableModelRoleConfig,
  shouldUseDeterministicModelTestFallback,
  summarizeModelError
} from "./modelErrors";

const structuredProbeSchema = z.object({
  ok: z.literal("ok")
});

const plainProbeMessages = [
  {
    role: "system" as const,
    content: "Reply with OK only."
  },
  {
    role: "user" as const,
    content: "OK"
  }
];

const structuredProbeMessages = [
  {
    role: "system" as const,
    content: 'Return a JSON object with {"ok":"ok"} only.'
  },
  {
    role: "user" as const,
    content: 'Return {"ok":"ok"}.'
  }
];

export interface ModelHealthServiceDependencies {
  now?: () => Date;
  probeOpenAICompatible?: (config: ModelRoleConfig) => Promise<void>;
  probeLangChainChat?: (config: ModelRoleConfig) => Promise<void>;
  probeLangChainStructuredGate?: (config: ModelRoleConfig) => Promise<void>;
  probeLangChainStructuredMemory?: (config: ModelRoleConfig) => Promise<void>;
}

export interface ModelHealthService {
  inspectRole(role: ModelRoleKey, config: ModelRoleConfig): PublicModelHealth;
  inspectAll(config: Pick<AppConfig, ModelRoleKey>): PublicModelHealthMap;
  testRoleConnection(role: ModelConnectionTestInput["role"], config: ModelRoleConfig): Promise<PublicModelHealth>;
}

export function createModelHealthService(
  dependencies: ModelHealthServiceDependencies = {}
): ModelHealthService {
  const now = dependencies.now ?? (() => new Date());

  return {
    inspectRole(role, config) {
      return inspectRoleHealth(role, config);
    },
    inspectAll(config) {
      return {
        chatModel: inspectRoleHealth("chatModel", config.chatModel),
        gateModel: inspectRoleHealth("gateModel", config.gateModel),
        memoryModel: inspectRoleHealth("memoryModel", config.memoryModel)
      };
    },
    async testRoleConnection(role, config) {
      const inspected = inspectRoleHealth(role, config);

      if (inspected.state === "not_configured" || inspected.state === "using_test_double") {
        return inspected;
      }

      const checkedAt = now().toISOString();

      try {
        await probeRole(role, config, dependencies);

        return {
          ...inspected,
          state: "connected",
          lastCheckedAt: checkedAt,
          lastErrorCode: undefined,
          lastErrorMessage: undefined
        };
      } catch (error) {
        const summary = summarizeModelError(error);

        return {
          ...inspected,
          state: "error",
          lastCheckedAt: checkedAt,
          lastErrorCode: summary.code,
          lastErrorMessage: summary.message
        };
      }
    }
  };
}

function inspectRoleHealth(role: ModelRoleKey, config: ModelRoleConfig): PublicModelHealth {
  const base = {
    role,
    provider: config.provider,
    enabled: config.enabled,
    configured: false,
    hasApiKey: Boolean(config.apiKey),
    model: config.model,
    baseUrl: config.baseUrl
  } satisfies Omit<PublicModelHealth, "state">;

  if (config.provider === "local-stub" && shouldUseDeterministicModelTestFallback()) {
    return {
      ...base,
      configured: true,
      state: "using_test_double"
    };
  }

  if (!isUsableModelRoleConfig(config)) {
    const summary = summarizeModelError(createMissingModelConfigurationError(role, config));

    return {
      ...base,
      state: "not_configured",
      lastErrorCode: summary.code,
      lastErrorMessage: summary.message
    };
  }

  if (config.provider !== "openai-compatible" && config.provider !== "langchain-openai") {
    const summary = summarizeModelError(createUnsupportedModelProviderError(role, config.provider));

    return {
      ...base,
      state: "error",
      lastErrorCode: summary.code,
      lastErrorMessage: summary.message
    };
  }

  return {
    ...base,
    configured: true,
    state: "configured"
  };
}

async function probeRole(
  role: ModelRoleKey,
  config: ModelRoleConfig,
  dependencies: ModelHealthServiceDependencies
): Promise<void> {
  if (config.provider === "openai-compatible") {
    return (dependencies.probeOpenAICompatible ?? defaultOpenAICompatibleProbe)(config);
  }

  if (config.provider !== "langchain-openai") {
    throw createUnsupportedModelProviderError(role, config.provider);
  }

  if (role === "chatModel") {
    return (dependencies.probeLangChainChat ?? defaultLangChainChatProbe)(config);
  }

  if (role === "gateModel") {
    return (dependencies.probeLangChainStructuredGate ?? defaultLangChainStructuredGateProbe)(config);
  }

  return (dependencies.probeLangChainStructuredMemory ?? defaultLangChainStructuredMemoryProbe)(config);
}

async function defaultOpenAICompatibleProbe(config: ModelRoleConfig): Promise<void> {
  const client = new OpenAICompatibleChatCompletionClient();
  await client.complete(buildPlainProbeRequest(config));
}

async function defaultLangChainChatProbe(config: ModelRoleConfig): Promise<void> {
  const client = new LangChainOpenAIChatCompletionClient();
  await client.complete(buildPlainProbeRequest(config));
}

async function defaultLangChainStructuredGateProbe(config: ModelRoleConfig): Promise<void> {
  const client = new LangChainStructuredGateClient({ schema: structuredProbeSchema });
  await client.complete(buildStructuredProbeRequest(config));
}

async function defaultLangChainStructuredMemoryProbe(config: ModelRoleConfig): Promise<void> {
  const client = new LangChainStructuredMemoryClient({ schema: structuredProbeSchema });
  await client.complete(buildStructuredProbeRequest(config));
}

function buildPlainProbeRequest(config: ModelRoleConfig): ChatCompletionRequest {
  return {
    baseUrl: config.baseUrl ?? "",
    apiKey: config.apiKey ?? "",
    model: config.model,
    temperature: 0,
    timeoutMs: config.timeoutMs,
    messages: plainProbeMessages
  };
}

function buildStructuredProbeRequest(config: ModelRoleConfig): ChatCompletionRequest {
  return {
    baseUrl: config.baseUrl ?? "",
    apiKey: config.apiKey ?? "",
    model: config.model,
    temperature: 0,
    timeoutMs: config.timeoutMs,
    messages: structuredProbeMessages
  };
}
