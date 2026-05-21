import type { ModelRoleConfig } from "../../../shared/src/schemas/config";
import type { ModelHealthErrorCode } from "../../../shared/src/contracts/settings";

export type ModelRoleName = "chatModel" | "gateModel" | "memoryModel" | "voiceAdapter";

export class ModelConfigurationError extends Error {
  readonly code = "MODEL_CONFIGURATION_ERROR";
  readonly role: ModelRoleName;

  constructor(role: ModelRoleName, message: string) {
    super(`[${role}] ${message}`);
    this.name = "ModelConfigurationError";
    this.role = role;
  }
}

export class ModelInvocationError extends Error {
  readonly code = "MODEL_INVOCATION_ERROR";
  readonly role: ModelRoleName;

  constructor(role: ModelRoleName, error: unknown) {
    super(`[${role}] ${formatModelError(error)}`);
    this.name = "ModelInvocationError";
    this.role = role;
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

export interface ModelErrorSummary {
  code: ModelHealthErrorCode;
  message: string;
}

export function summarizeModelError(error: unknown): ModelErrorSummary {
  if (error instanceof ModelConfigurationError) {
    const normalized = error.message.toLowerCase();

    if (normalized.includes("unsupported model provider")) {
      return {
        code: "unsupported_provider",
        message: "Unsupported model provider."
      };
    }

    return {
      code: "missing_configuration",
      message: "Model configuration is incomplete."
    };
  }

  const message = formatModelError(error);
  const normalized = message.toLowerCase();

  if (/\b401\b|\b403\b|unauthorized|forbidden|invalid api key/u.test(normalized)) {
    return {
      code: "authentication_failed",
      message: "API key is invalid or lacks permission."
    };
  }

  if (/timeout|timed out|abort|aborted/u.test(normalized)) {
    return {
      code: "timeout",
      message: "Model request timed out."
    };
  }

  if (/fetch failed|network|econnrefused|enotfound|failed to fetch/u.test(normalized)) {
    return {
      code: "network_error",
      message: "Could not reach the model service."
    };
  }

  if (/did not include content|invalid json|structured output|schema|malformed/u.test(normalized)) {
    return {
      code: "malformed_response",
      message: "Model response format was invalid."
    };
  }

  return {
    code: "invocation_failed",
    message: "Model request failed."
  };
}

function formatModelError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
