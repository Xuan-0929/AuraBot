/**
 * 定义 Renderer 可见的非敏感设置与模型健康契约。
 *
 * Main process 从 AppConfig 投影模型和主动能力设置，隐藏 API key；该公共边界不再
 * 暴露任何 Task runner、CLI command、sandbox 或 approval 配置。
 */

import type { AppConfig, ModelRoleConfig, ProactiveConfig } from "../schemas/config";

export const MODEL_PROVIDER_OPTIONS = ["openai-compatible", "langchain-openai"] as const;
export const MODEL_HEALTH_STATES = [
  "not_configured",
  "configured",
  "testing",
  "connected",
  "error",
  "using_test_double"
] as const;
export const MODEL_HEALTH_ERROR_CODES = [
  "missing_configuration",
  "unsupported_provider",
  "authentication_failed",
  "timeout",
  "network_error",
  "malformed_response",
  "invocation_failed"
] as const;

export type ModelProviderOption = (typeof MODEL_PROVIDER_OPTIONS)[number];
export type ModelRoleKey = "chatModel" | "gateModel" | "memoryModel";
export type ModelHealthState = (typeof MODEL_HEALTH_STATES)[number];
export type ModelHealthErrorCode = (typeof MODEL_HEALTH_ERROR_CODES)[number];

export interface PublicModelRoleConfig {
  provider: string;
  model: string;
  enabled: boolean;
  baseUrl?: string;
  temperature?: number;
  timeoutMs?: number;
  hasApiKey: boolean;
}

export interface PublicModelHealth {
  role: ModelRoleKey;
  provider: string;
  enabled: boolean;
  configured: boolean;
  hasApiKey: boolean;
  model?: string;
  baseUrl?: string;
  state: ModelHealthState;
  lastCheckedAt?: string;
  lastErrorCode?: ModelHealthErrorCode;
  lastErrorMessage?: string;
}

export type PublicModelHealthMap = Record<ModelRoleKey, PublicModelHealth>;

export interface PublicAppConfig {
  chatModel: PublicModelRoleConfig;
  gateModel: PublicModelRoleConfig;
  memoryModel: PublicModelRoleConfig;
  proactive: PublicProactiveConfig;
}

export type PublicProactiveConfig = Pick<
  ProactiveConfig,
  "pollIntervalMs" | "followUpLookaheadMs" | "idleThresholdMs" | "idleCooldownMs"
>;

export type ProactiveSettingsPatch = Partial<
  Pick<ProactiveConfig, "idleThresholdMs" | "idleCooldownMs">
>;

export interface ModelProviderSettingsPatch {
  role: ModelRoleKey;
  provider: ModelProviderOption;
}

export interface ModelConnectionTestInput {
  role: ModelRoleKey;
}

export function createPublicAppConfig(config: AppConfig): PublicAppConfig {
  return {
    chatModel: createPublicModelRoleConfig(config.chatModel),
    gateModel: createPublicModelRoleConfig(config.gateModel),
    memoryModel: createPublicModelRoleConfig(config.memoryModel),
    proactive: {
      pollIntervalMs: config.proactive.pollIntervalMs,
      followUpLookaheadMs: config.proactive.followUpLookaheadMs,
      idleThresholdMs: config.proactive.idleThresholdMs,
      idleCooldownMs: config.proactive.idleCooldownMs
    }
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
