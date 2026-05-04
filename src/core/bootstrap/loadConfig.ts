import { join } from "node:path";
import { existsSync, readFileSync } from "node:fs";
import { appConfigSchema, type AppConfig } from "../../shared/schemas/config";

type AppConfigInput = Partial<Omit<AppConfig, "gateModel" | "chatModel" | "memoryModel" | "taskRunner">> & {
  gateModel?: Partial<AppConfig["gateModel"]>;
  chatModel?: Partial<AppConfig["chatModel"]>;
  memoryModel?: Partial<AppConfig["memoryModel"]>;
  taskRunner?: Partial<AppConfig["taskRunner"]>;
};

export function loadConfig(userDataPath: string): AppConfig {
  const defaults: AppConfig = {
    storagePath: join(userDataPath, "aurabot.sqlite"),
    debugLogging: true,
    gateModel: {
      provider: "local-stub",
      model: "gate-stub",
      enabled: false
    },
    chatModel: {
      provider: "local-stub",
      model: "chat-stub",
      enabled: false
    },
    memoryModel: {
      provider: "local-stub",
      model: "memory-stub",
      enabled: false
    },
    taskRunner: {
      provider: "deterministic",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000
    }
  };
  const fileConfig = loadConfigFile(userDataPath);
  const fileMerged = appConfigSchema.parse(mergeConfig(defaults, fileConfig));
  const merged = mergeConfig(fileMerged, loadEnvironmentConfig());

  return appConfigSchema.parse(merged);
}

function loadConfigFile(userDataPath: string): AppConfigInput {
  const configPath = getConfigCandidatePaths(userDataPath).find((candidatePath) => existsSync(candidatePath));

  if (!configPath) {
    return {};
  }

  return JSON.parse(readFileSync(configPath, "utf8")) as AppConfigInput;
}

function getConfigCandidatePaths(userDataPath: string): string[] {
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
    gateModel: {
      ...defaults.gateModel,
      ...override.gateModel
    },
    chatModel: {
      ...defaults.chatModel,
      ...override.chatModel
    },
    memoryModel: {
      ...defaults.memoryModel,
      ...override.memoryModel
    },
    taskRunner: {
      ...defaults.taskRunner,
      ...override.taskRunner
    }
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
    taskRunner: readTaskRunnerEnvironment()
  };
}

function readModelRoleEnvironment(
  role: "GATE" | "CHAT" | "MEMORY",
  common: {
    provider?: string;
    baseUrl?: string;
    apiKey?: string;
  }
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
  if (value === undefined) {
    return undefined;
  }

  return ["1", "true", "yes", "on"].includes(value.toLowerCase());
}

function parseNumberEnvironment(value: string | undefined): number | undefined {
  if (value === undefined) {
    return undefined;
  }

  const parsed = Number(value);

  return Number.isFinite(parsed) ? parsed : undefined;
}

function readTaskRunnerEnvironment(): AppConfigInput["taskRunner"] {
  return removeUndefinedValues({
    provider: readTaskRunnerProviderEnvironment(),
    codexCommand: process.env.AURABOT_CODEX_COMMAND,
    workingDirectory: process.env.AURABOT_CODEX_WORKDIR,
    model: process.env.AURABOT_CODEX_MODEL,
    sandbox: readCodexSandboxEnvironment(),
    approvalPolicy: readCodexApprovalPolicyEnvironment(),
    timeoutMs: parseNumberEnvironment(process.env.AURABOT_CODEX_TIMEOUT_MS)
  });
}

function readTaskRunnerProviderEnvironment(): AppConfig["taskRunner"]["provider"] | undefined {
  const value = process.env.AURABOT_TASK_RUNNER_PROVIDER ?? process.env.AURABOT_TASK_RUNNER;

  return value as AppConfig["taskRunner"]["provider"] | undefined;
}

function readCodexSandboxEnvironment(): AppConfig["taskRunner"]["sandbox"] | undefined {
  return process.env.AURABOT_CODEX_SANDBOX as AppConfig["taskRunner"]["sandbox"] | undefined;
}

function readCodexApprovalPolicyEnvironment(): AppConfig["taskRunner"]["approvalPolicy"] | undefined {
  return process.env.AURABOT_CODEX_APPROVAL_POLICY as AppConfig["taskRunner"]["approvalPolicy"] | undefined;
}

function removeUndefinedValues<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, entryValue]) => entryValue !== undefined)) as Partial<T>;
}
