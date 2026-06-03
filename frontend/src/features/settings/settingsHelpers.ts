import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import {
  MODEL_PROVIDER_OPTIONS,
  type ModelProviderOption,
  type PublicModelHealth
} from "@shared/contracts/settings";
import type { PetSettings } from "@shared/schemas/petSettings";
import { t } from "../../i18n";

export const DEFAULT_TASK_TIMEOUT_MS = 300000;

export function splitLines(value: string): string[] {
  return value
    .split(/\r?\n/u)
    .map((line) => line.trim())
    .filter(Boolean);
}

export function millisecondsToMinutes(value: number): number {
  return Math.max(1, Math.round(value / 60_000));
}

export function minutesToMilliseconds(value: number): number {
  return Math.max(1, value) * 60_000;
}

export function toTimeoutMinutes(timeoutMs: number | undefined): number {
  return Math.max(1, Math.round((timeoutMs ?? DEFAULT_TASK_TIMEOUT_MS) / 60000));
}

export function toModelProviderOption(provider: string): ModelProviderOption {
  return MODEL_PROVIDER_OPTIONS.includes(provider as ModelProviderOption)
    ? (provider as ModelProviderOption)
    : "openai-compatible";
}

export function getModelProviderLabel(
  provider: ModelProviderOption,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (provider === "langchain-openai") {
    return t(displayLanguage, "providerLangChainOpenAI");
  }

  return t(displayLanguage, "providerOpenAICompatible");
}

export function modelHealthLabel(
  health: PublicModelHealth | undefined,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (!health) {
    return t(displayLanguage, "modelStateNotConfigured");
  }

  switch (health.state) {
    case "connected":
      return t(displayLanguage, "modelStateConnected");
    case "configured":
      return t(displayLanguage, "modelStateConfigured");
    case "testing":
      return t(displayLanguage, "modelStateTesting");
    case "error":
      return t(displayLanguage, "modelStateError");
    case "using_test_double":
      return t(displayLanguage, "modelStateUsingTestDouble");
    default:
      return t(displayLanguage, "modelStateNotConfigured");
  }
}

export function modelHealthErrorLabel(
  code: PublicModelHealth["lastErrorCode"],
  displayLanguage: PetSettings["displayLanguage"]
): string {
  switch (code) {
    case "unsupported_provider":
      return t(displayLanguage, "modelErrorUnsupportedProvider");
    case "authentication_failed":
      return t(displayLanguage, "modelErrorAuthenticationFailed");
    case "timeout":
      return t(displayLanguage, "modelErrorTimeout");
    case "network_error":
      return t(displayLanguage, "modelErrorNetwork");
    case "malformed_response":
      return t(displayLanguage, "modelErrorMalformedResponse");
    case "invocation_failed":
      return t(displayLanguage, "modelErrorInvocationFailed");
    default:
      return t(displayLanguage, "modelErrorMissingConfiguration");
  }
}

export function testConnectionAriaLabel(label: string, displayLanguage: PetSettings["displayLanguage"]): string {
  if (displayLanguage === "zh") {
    return `测试${label}连接`;
  }

  return `Test ${label} connection`;
}

export function notificationPermissionLabel(
  value: NotificationSettings["permissionState"] | undefined,
  displayLanguage: PetSettings["displayLanguage"]
): string {
  if (value === "unsupported") {
    return t(displayLanguage, "notificationPermissionUnsupported");
  }

  return t(displayLanguage, "notificationPermissionSystemManaged");
}
