/**
 * 持久化 Renderer 可修改的模型 provider 与非 Task 主动能力设置。
 *
 * 写入时保留用户 JSON 中的未知字段（包括历史 taskRunner），但不读取、合并或更新
 * 它们；返回值始终通过当前 AppConfig schema 校验。
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { appConfigSchema, type AppConfig } from "@shared/schemas/config";
import type {
  ModelProviderSettingsPatch,
  ProactiveSettingsPatch
} from "@shared/contracts/settings";

type UserConfigFile = Record<string, unknown> & {
  gateModel?: Partial<AppConfig["gateModel"]>;
  chatModel?: Partial<AppConfig["chatModel"]>;
  memoryModel?: Partial<AppConfig["memoryModel"]>;
  proactive?: Partial<AppConfig["proactive"]>;
};

export function updateUserModelProviderSettings(
  userDataPath: string,
  currentConfig: AppConfig,
  patch: ModelProviderSettingsPatch
): AppConfig {
  const configPath = join(userDataPath, "config.json");
  const userConfig = readUserConfigFile(configPath);
  const roleConfig = {
    ...currentConfig[patch.role],
    ...(userConfig[patch.role] ?? {}),
    provider: patch.provider,
    enabled: true
  };
  writeUserConfig(userDataPath, configPath, {
    ...userConfig,
    [patch.role]: roleConfig
  });
  return appConfigSchema.parse({ ...currentConfig, [patch.role]: roleConfig });
}

export function updateUserProactiveSettings(
  userDataPath: string,
  currentConfig: AppConfig,
  patch: ProactiveSettingsPatch
): AppConfig {
  const configPath = join(userDataPath, "config.json");
  const userConfig = readUserConfigFile(configPath);
  const proactive = {
    ...currentConfig.proactive,
    ...(userConfig.proactive ?? {}),
    ...patch
  };
  writeUserConfig(userDataPath, configPath, { ...userConfig, proactive });
  return appConfigSchema.parse({ ...currentConfig, proactive });
}

function readUserConfigFile(configPath: string): UserConfigFile {
  return existsSync(configPath)
    ? (JSON.parse(readFileSync(configPath, "utf8")) as UserConfigFile)
    : {};
}

function writeUserConfig(
  userDataPath: string,
  configPath: string,
  config: UserConfigFile
): void {
  mkdirSync(userDataPath, { recursive: true });
  writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}
