import { join } from "node:path";
import { existsSync, readFileSync } from "node:fs";
import {
  appConfigSchema,
  type AppConfig,
  type TaskCliRunnerConfig,
  type TaskCliRunnerId,
  type TaskRunnerConfig
} from "../../../shared/src/schemas/config";
import { detectTaskCliCommand } from "../tasks/taskCliCommandDiscovery";

type AppConfigInput = Partial<
  Omit<AppConfig, "gateModel" | "chatModel" | "memoryModel" | "taskRunner" | "proactive">
> & {
  gateModel?: Partial<AppConfig["gateModel"]>;
  chatModel?: Partial<AppConfig["chatModel"]>;
  memoryModel?: Partial<AppConfig["memoryModel"]>;
  taskRunner?: Partial<AppConfig["taskRunner"]>;
  proactive?: Partial<AppConfig["proactive"]>;
};

export function loadConfig(userDataPath: string): AppConfig {
  const defaults: AppConfig = {
    storagePath: join(userDataPath, "aurabot.sqlite"),
    debugLogging: true,
    gateModel: {
      provider: "openai-compatible",
      model: "gate-model",
      enabled: false
    },
    chatModel: {
      provider: "openai-compatible",
      model: "chat-model",
      enabled: false
    },
    memoryModel: {
      provider: "openai-compatible",
      model: "memory-model",
      enabled: false
    },
    taskRunner: {
      provider: "deterministic",
      runners: createDefaultTaskCliRunners(),
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000
    },
    proactive: {
      pollIntervalMs: 10000,
      followUpLookaheadMs: 0,
      idleThresholdMs: 600000,
      idleCooldownMs: 1800000
    }
  };
  const fileConfig = loadConfigFile(userDataPath);
  const fileMerged = normalizeAppConfig(appConfigSchema.parse(mergeConfig(defaults, fileConfig)), fileConfig.taskRunner);
  const environmentConfig = loadEnvironmentConfig();
  const merged = normalizeAppConfig(appConfigSchema.parse(mergeConfig(fileMerged, environmentConfig)), environmentConfig.taskRunner);

  return appConfigSchema.parse(merged);
}

function loadConfigFile(userDataPath: string): AppConfigInput {
  const configPath = getConfigCandidatePaths(userDataPath).find((candidatePath) => existsSync(candidatePath));

  if (!configPath) {
    return {};
  }

  return JSON.parse(readFileSync(configPath, "utf8")) as AppConfigInput;
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
  const taskRunner = mergeTaskRunnerConfig(defaults.taskRunner, override.taskRunner);

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
    taskRunner,
    proactive: {
      ...defaults.proactive,
      ...override.proactive
    }
  };
}

function mergeTaskRunnerConfig(
  defaults: TaskRunnerConfig,
  override: AppConfigInput["taskRunner"]
): AppConfigInput["taskRunner"] {
  if (!override) {
    return defaults;
  }

  return {
    ...defaults,
    ...override,
    runners: override.runners ? mergeTaskCliRunners(defaults.runners, override.runners) : defaults.runners
  };
}

function mergeTaskCliRunners(
  defaults: TaskCliRunnerConfig[],
  overrides: NonNullable<AppConfigInput["taskRunner"]>["runners"]
): TaskCliRunnerConfig[] {
  const byId = new Map<TaskCliRunnerId, TaskCliRunnerConfig>(
    defaults.map((runner) => [runner.id, runner])
  );

  for (const override of overrides ?? []) {
    const id = override?.id;

    if (!id) {
      continue;
    }

    byId.set(id, {
      ...byId.get(id),
      ...override
    } as TaskCliRunnerConfig);
  }

  return createDefaultTaskCliRunners().map((runner) => ({
    ...runner,
    ...byId.get(runner.id)
  }));
}

function normalizeAppConfig(config: AppConfig, taskRunnerOverride?: AppConfigInput["taskRunner"]): AppConfig {
  return {
    ...config,
    gateModel: normalizeModelRoleConfig(config.gateModel, "gate-model"),
    chatModel: normalizeModelRoleConfig(config.chatModel, "chat-model"),
    memoryModel: normalizeModelRoleConfig(config.memoryModel, "memory-model"),
    taskRunner: normalizeTaskRunnerConfig(config.taskRunner, taskRunnerOverride)
  };
}

function normalizeModelRoleConfig(config: AppConfig["chatModel"], defaultModel: string): AppConfig["chatModel"] {
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

function normalizeTaskRunnerConfig(
  config: TaskRunnerConfig,
  taskRunnerOverride?: AppConfigInput["taskRunner"]
): TaskRunnerConfig {
  const runners = mergeTaskCliRunners(createDefaultTaskCliRunners(), config.runners);
  const hasExplicitRunners = Boolean(taskRunnerOverride?.runners);

  if (config.provider === "codex" && !hasExplicitRunners) {
    return {
      ...config,
      runners: runners.map((runner) =>
        runner.id === "codex"
          ? {
              ...runner,
              enabled: true,
              priority: 1,
              command: config.codexCommand ?? runner.command,
              workingDirectory: config.workingDirectory ?? runner.workingDirectory,
              model: config.model ?? runner.model,
              sandbox: config.sandbox,
              approvalPolicy: config.approvalPolicy,
              timeoutMs: config.timeoutMs
            }
          : runner
      )
    };
  }

  return {
    ...config,
    provider: runners.some((runner) => runner.enabled) ? "cli" : config.provider,
    runners
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
    taskRunner: readTaskRunnerEnvironment(),
    proactive: readProactiveEnvironment()
  };
}

function readProactiveEnvironment(): AppConfigInput["proactive"] {
  return removeUndefinedValues({
    pollIntervalMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_POLL_INTERVAL_MS),
    followUpLookaheadMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_FOLLOW_UP_LOOKAHEAD_MS),
    idleThresholdMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_IDLE_THRESHOLD_MS),
    idleCooldownMs: parseNumberEnvironment(process.env.AURABOT_PROACTIVE_IDLE_COOLDOWN_MS)
  });
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
    timeoutMs: parseNumberEnvironment(process.env.AURABOT_CODEX_TIMEOUT_MS),
    runners: readTaskRunnerCliEnvironment()
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

function readTaskRunnerCliEnvironment(): TaskCliRunnerConfig[] | undefined {
  const hasRunnerEnvironment = createDefaultTaskCliRunners().some((runner) => {
    const prefix = runner.id.toUpperCase();

    return [
      `AURABOT_${prefix}_RUNNER_ENABLED`,
      `AURABOT_${prefix}_RUNNER_PRIORITY`,
      runner.id === "codex" ? undefined : `AURABOT_${prefix}_COMMAND`,
      runner.id === "codex" ? undefined : `AURABOT_${prefix}_WORKDIR`,
      runner.id === "codex" ? undefined : `AURABOT_${prefix}_MODEL`,
      `AURABOT_${prefix}_TIMEOUT_MS`
    ].some((key) => key !== undefined && process.env[key] !== undefined);
  });

  if (!hasRunnerEnvironment) {
    return undefined;
  }

  const runners = createDefaultTaskCliRunners().map((runner) =>
    removeUndefinedValues({
      ...runner,
      enabled: parseBooleanEnvironment(process.env[`AURABOT_${runner.id.toUpperCase()}_RUNNER_ENABLED`]),
      priority: parseNumberEnvironment(process.env[`AURABOT_${runner.id.toUpperCase()}_RUNNER_PRIORITY`]),
      command: process.env[`AURABOT_${runner.id.toUpperCase()}_COMMAND`],
      workingDirectory: process.env[`AURABOT_${runner.id.toUpperCase()}_WORKDIR`],
      model: process.env[`AURABOT_${runner.id.toUpperCase()}_MODEL`],
      timeoutMs: parseNumberEnvironment(process.env[`AURABOT_${runner.id.toUpperCase()}_TIMEOUT_MS`])
    }) as TaskCliRunnerConfig
  );

  return runners;
}

export function createDefaultTaskCliRunners(): TaskCliRunnerConfig[] {
  return [
    {
      id: "codex",
      enabled: false,
      priority: 1,
      command: detectTaskCliCommand("codex"),
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      extraArgs: []
    },
    {
      id: "claude",
      enabled: false,
      priority: 2,
      command: detectTaskCliCommand("claude"),
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      extraArgs: []
    },
    {
      id: "hermes",
      enabled: false,
      priority: 3,
      command: detectTaskCliCommand("hermes"),
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 300000,
      extraArgs: []
    }
  ];
}

function removeUndefinedValues<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(Object.entries(value).filter(([, entryValue]) => entryValue !== undefined)) as Partial<T>;
}
