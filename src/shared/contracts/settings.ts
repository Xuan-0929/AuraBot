import type {
  AppConfig,
  ModelRoleConfig,
  ProactiveConfig,
  TaskCliRunnerConfig,
  TaskCliRunnerId
} from "../schemas/config";

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
  taskRunner: PublicTaskRunnerConfig;
  proactive: PublicProactiveConfig;
}

export interface PublicTaskRunnerConfig {
  runners: PublicTaskCliRunnerConfig[];
}

export interface PublicTaskCliRunnerConfig {
  id: TaskCliRunnerId;
  label: string;
  enabled: boolean;
  priority: number;
  command?: string;
  workingDirectory?: string;
}

export type PublicProactiveConfig = Pick<
  ProactiveConfig,
  "pollIntervalMs" | "followUpLookaheadMs" | "idleThresholdMs" | "idleCooldownMs"
>;

export interface TaskRunnerSettingsPatch {
  runners: Array<{
    id: TaskCliRunnerId;
    enabled: boolean;
    priority: number;
  }>;
}

export type ProactiveSettingsPatch = Partial<Pick<ProactiveConfig, "idleThresholdMs" | "idleCooldownMs">>;

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
    taskRunner: {
      runners: config.taskRunner.runners.map(createPublicTaskCliRunnerConfig)
    },
    proactive: createPublicProactiveConfig(config.proactive)
  };
}

function createPublicProactiveConfig(config: ProactiveConfig): PublicProactiveConfig {
  return {
    pollIntervalMs: config.pollIntervalMs,
    followUpLookaheadMs: config.followUpLookaheadMs,
    idleThresholdMs: config.idleThresholdMs,
    idleCooldownMs: config.idleCooldownMs
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

function createPublicTaskCliRunnerConfig(config: TaskCliRunnerConfig): PublicTaskCliRunnerConfig {
  return {
    id: config.id,
    label: getTaskCliRunnerLabel(config.id),
    enabled: config.enabled,
    priority: config.priority,
    command: config.command,
    workingDirectory: config.workingDirectory
  };
}

export function getTaskCliRunnerLabel(id: TaskCliRunnerId): string {
  if (id === "codex") {
    return "Codex";
  }

  if (id === "claude") {
    return "Claude Code";
  }

  return "Hermes";
}
