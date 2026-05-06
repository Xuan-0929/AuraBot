import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { appConfigSchema, type AppConfig, type TaskCliRunnerConfig } from "../shared/schemas/config";
import type { ModelProviderSettingsPatch, TaskRunnerSettingsPatch } from "../shared/contracts/settings";

type UserConfigFile = Partial<Omit<AppConfig, "taskRunner">> & {
  taskRunner?: Partial<AppConfig["taskRunner"]>;
};

export function updateUserTaskRunnerSettings(
  userDataPath: string,
  currentConfig: AppConfig,
  patch: TaskRunnerSettingsPatch
): AppConfig {
  const configPath = join(userDataPath, "config.json");
  const userConfig = readUserConfigFile(configPath);
  const runners = currentConfig.taskRunner.runners.map((runner) => {
    const updated = patch.runners.find((entry) => entry.id === runner.id);

    return updated
      ? {
          ...runner,
          enabled: updated.enabled,
          priority: updated.priority
        }
      : runner;
  });
  const provider: AppConfig["taskRunner"]["provider"] = runners.some((runner) => runner.enabled)
    ? "cli"
    : "deterministic";
  const taskRunner = {
    ...currentConfig.taskRunner,
    ...userConfig.taskRunner,
    provider,
    runners: mergePersistedRunnerDetails(runners, userConfig.taskRunner?.runners)
  };
  const nextUserConfig: UserConfigFile = {
    ...userConfig,
    taskRunner
  };

  mkdirSync(userDataPath, { recursive: true });
  writeFileSync(configPath, `${JSON.stringify(nextUserConfig, null, 2)}\n`, "utf8");

  return appConfigSchema.parse({
    ...currentConfig,
    taskRunner
  });
}

export function updateUserModelProviderSettings(
  userDataPath: string,
  currentConfig: AppConfig,
  patch: ModelProviderSettingsPatch
): AppConfig {
  const configPath = join(userDataPath, "config.json");
  const userConfig = readUserConfigFile(configPath);
  const currentRoleConfig = currentConfig[patch.role];
  const persistedRoleConfig = userConfig[patch.role] ?? {};
  const roleConfig = {
    ...currentRoleConfig,
    ...persistedRoleConfig,
    provider: patch.provider,
    enabled: patch.provider !== "local-stub"
  };
  const nextUserConfig: UserConfigFile = {
    ...userConfig,
    [patch.role]: roleConfig
  };

  mkdirSync(userDataPath, { recursive: true });
  writeFileSync(configPath, `${JSON.stringify(nextUserConfig, null, 2)}\n`, "utf8");

  return appConfigSchema.parse({
    ...currentConfig,
    [patch.role]: roleConfig
  });
}

function readUserConfigFile(configPath: string): UserConfigFile {
  if (!existsSync(configPath)) {
    return {};
  }

  return JSON.parse(readFileSync(configPath, "utf8")) as UserConfigFile;
}

function mergePersistedRunnerDetails(
  runners: TaskCliRunnerConfig[],
  persistedRunners: TaskCliRunnerConfig[] | undefined
): TaskCliRunnerConfig[] {
  return runners.map((runner) => {
    const persisted = persistedRunners?.find((entry) => entry.id === runner.id);

    return persisted
      ? {
          ...runner,
          command: persisted.command ?? runner.command,
          workingDirectory: persisted.workingDirectory ?? runner.workingDirectory,
          model: persisted.model ?? runner.model,
          sandbox: persisted.sandbox ?? runner.sandbox,
          approvalPolicy: persisted.approvalPolicy ?? runner.approvalPolicy,
          timeoutMs: persisted.timeoutMs ?? runner.timeoutMs,
          extraArgs: persisted.extraArgs ?? runner.extraArgs
        }
      : runner;
  });
}
