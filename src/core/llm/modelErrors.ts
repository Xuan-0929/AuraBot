import type { ModelRoleConfig } from "../../shared/schemas/config";

export type ModelRoleName = "chatModel" | "gateModel" | "memoryModel" | "voiceAdapter";

export class ModelConfigurationError extends Error {
  readonly code = "MODEL_CONFIGURATION_ERROR";

  constructor(role: ModelRoleName, message: string) {
    super(`[${role}] ${message}`);
    this.name = "ModelConfigurationError";
  }
}

export class ModelInvocationError extends Error {
  readonly code = "MODEL_INVOCATION_ERROR";

  constructor(role: ModelRoleName, error: unknown) {
    super(`[${role}] ${formatModelError(error)}`);
    this.name = "ModelInvocationError";
  }
}

export function isUsableModelRoleConfig(config: ModelRoleConfig): boolean {
  return config.enabled && Boolean(config.baseUrl && config.apiKey);
}

export function createMissingModelConfigurationError(
  role: ModelRoleName,
  config: ModelRoleConfig
): ModelConfigurationError {
  const missing = [
    config.enabled ? undefined : "enabled",
    config.baseUrl ? undefined : "baseUrl",
    config.apiKey ? undefined : "apiKey"
  ].filter(Boolean);

  return new ModelConfigurationError(
    role,
    `model API is not configured${missing.length > 0 ? `; missing ${missing.join(", ")}` : ""}.`
  );
}

export function createUnsupportedModelProviderError(
  role: ModelRoleName,
  provider: string
): ModelConfigurationError {
  return new ModelConfigurationError(role, `unsupported model provider: ${provider}.`);
}

export function shouldUseDeterministicModelTestFallback(): boolean {
  return process.env.NODE_ENV === "test" && process.env.AURABOT_DETERMINISTIC_MODEL_FALLBACK === "1";
}

function formatModelError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
