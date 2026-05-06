import type { AppConfig, ModelRoleConfig } from "../schemas/config";

export interface PublicModelRoleConfig {
  provider: string;
  model: string;
  enabled: boolean;
  baseUrl?: string;
  temperature?: number;
  timeoutMs?: number;
  hasApiKey: boolean;
}

export interface PublicAppConfig {
  chatModel: PublicModelRoleConfig;
  gateModel: PublicModelRoleConfig;
  memoryModel: PublicModelRoleConfig;
}

export function createPublicAppConfig(config: AppConfig): PublicAppConfig {
  return {
    chatModel: createPublicModelRoleConfig(config.chatModel),
    gateModel: createPublicModelRoleConfig(config.gateModel),
    memoryModel: createPublicModelRoleConfig(config.memoryModel)
  };
}

function createPublicModelRoleConfig(config: ModelRoleConfig): PublicModelRoleConfig {
  return {
    provider: config.provider,
    model: config.model,
    enabled: config.enabled,
    baseUrl: config.baseUrl,
    temperature: config.temperature,
    timeoutMs: config.timeoutMs,
    hasApiKey: Boolean(config.apiKey)
  };
}
