/**
 * 从默认值、用户 JSON 与模型/主动能力环境变量合并 Electron 配置。
 *
 * 加载器保留现有 config candidate 与模型兼容归一化，明确忽略旧 taskRunner 和
 * Codex/Claude/Hermes 环境变量，不修改磁盘上的历史 JSON。
 */

import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { appConfigSchema, type AppConfig } from "@shared/schemas/config";

type AppConfigInput = Partial<
  Omit<AppConfig, "gateModel" | "chatModel" | "memoryModel" | "proactive">
> & {
  gateModel?: Partial<AppConfig["gateModel"]>;
  chatModel?: Partial<AppConfig["chatModel"]>;
  memoryModel?: Partial<AppConfig["memoryModel"]>;
  proactive?: Partial<AppConfig["proactive"]>;
};

export function loadConfig(userDataPath: string): AppConfig {
  const defaults: AppConfig = {
    storagePath: join(userDataPath, "aurabot.sqlite"),
    debugLogging: true,
    gateModel: { provider: "openai-compatible", model: "gate-model", enabled: false },
    chatModel: { provider: "openai-compatible", model: "chat-model", enabled: false },
    memoryModel: { provider: "openai-compatible", model: "memory-model", enabled: false },
    proactive: {
      pollIntervalMs: 10000,
      followUpLookaheadMs: 0,
      idleThresholdMs: 600000,
      idleCooldownMs: 1800000
    }
  };
  const fileMerged = mergeConfig(defaults, loadConfigFile(userDataPath));
  const environmentMerged = mergeConfig(
    appConfigSchema.parse(fileMerged),
    loadEnvironmentConfig()
  );
  const parsed = appConfigSchema.parse(environmentMerged);

  return {
    ...parsed,
    gateModel: normalizeModelRoleConfig(parsed.gateModel, "gate-model"),
    chatModel: normalizeModelRoleConfig(parsed.chatModel, "chat-model"),
    memoryModel: normalizeModelRoleConfig(parsed.memoryModel, "memory-model")
  };
}

function loadConfigFile(userDataPath: string): AppConfigInput {
  const configPath = getConfigCandidatePaths(userDataPath).find(existsSync);
  return configPath
    ? (JSON.parse(readFileSync(configPath, "utf8")) as AppConfigInput)
    : {};
}

export function getConfigCandidatePaths(userDataPath: string): string[] {
  return [
    join(userDataPath, "aurabot.config.json"),
    join(userDataPath, "config.json"),
    ...(shouldLoadSystemConfig()
      ? [
          "/Library/Application Support/AuraBot/aurabot.config.json",
          "/Library/Application Support/AuraBot/config.json"
        ]
      : [])
  ];
}

function shouldLoadSystemConfig(): boolean {
  return (
    process.platform === "darwin" &&
    process.env.NODE_ENV !== "test" &&
    process.env.AURABOT_DISABLE_SYSTEM_CONFIG !== "1"
  );
}

function mergeConfig(defaults: AppConfig, override: AppConfigInput): AppConfigInput {
  return {
    ...defaults,
    ...override,
    gateModel: { ...defaults.gateModel, ...override.gateModel },
    chatModel: { ...defaults.chatModel, ...override.chatModel },
    memoryModel: { ...defaults.memoryModel, ...override.memoryModel },
    proactive: { ...defaults.proactive, ...override.proactive }
  };
}

function normalizeModelRoleConfig(
  config: AppConfig["chatModel"],
  defaultModel: string
): AppConfig["chatModel"] {
  if (config.provider !== "local-stub") {
    return config;
  }
  return {
    ...config,
    provider: "openai-compatible",
    model: /-stub$/u.test(config.model) ? defaultModel : config.model,
    enabled: false
  };
}

function loadEnvironmentConfig(): AppConfigInput {
  const common = {
    provider: process.env.AURABOT_MODEL_PROVIDER,
    baseUrl: process.env.AURABOT_MODEL_BASE_URL,
    apiKey: process.env.AURABOT_MODEL_API_KEY
  };
  return {
    gateModel: readModelRoleEnvironment("GATE", common),
    chatModel: readModelRoleEnvironment("CHAT", common),
    memoryModel: readModelRoleEnvironment("MEMORY", common),
    proactive: removeUndefinedValues({
      pollIntervalMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_POLL_INTERVAL_MS),
      followUpLookaheadMs: parseNumberEnvironment(
        process.env.AURABOT_PROACTIVE_FOLLOW_UP_LOOKAHEAD_MS
      ),
      idleThresholdMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_IDLE_THRESHOLD_MS),
      idleCooldownMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_IDLE_COOLDOWN_MS)
    })
  };
}

function readModelRoleEnvironment(
  role: "GATE" | "CHAT" | "MEMORY",
  common: { provider?: string; baseUrl?: string; apiKey?: string }
): AppConfigInput["gateModel"] {
  return removeUndefinedValues({
    provider: process.env[`AURABOT_${role}_MODEL_PROVIDER`] ?? common.provider,
    baseUrl: process.env[`AURABOT_${role}_MODEL_BASE_URL`] ?? common.baseUrl,
    apiKey: process.env[`AURABOT_${role}_MODEL_API_KEY`] ?? common.apiKey,
    model: process.env[`AURABOT_${role}_MODEL_NAME`] ?? process.env[`AURABOT_${role}_MODEL`],
    enabled: parseBooleanEnvironment(process.env[`AURABOT_${role}_MODEL_ENABLED`]),
    temperature: parseNumberEnvironment(process.env[`AURABOT_${role}_MODEL_TEMPERATURE`]),
    timeoutMs: parseNumberEnvironment(process.env[`AURABOT_${role}_MODEL_TIMEOUT_MS`])
  });
}

function parseBooleanEnvironment(value: string | undefined): boolean | undefined {
  return value === undefined
    ? undefined
    : ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function parseNumberEnvironment(value: string | undefined): number | undefined {
  if (value === undefined) {
    return undefined;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function removeUndefinedValues<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(value).filter(([, entryValue]) => entryValue !== undefined)
  ) as Partial<T>;
}
