import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { updateUserModelProviderSettings, updateUserProactiveSettings } from "../../../src/main/appConfigStore";
import type { AppConfig } from "../../../src/shared/schemas/config";

const baseConfig: AppConfig = {
  storagePath: "/tmp/aurabot.sqlite",
  debugLogging: true,
  gateModel: {
    provider: "openai-compatible",
    model: "gate-model",
    enabled: false
  },
  chatModel: {
    provider: "openai-compatible",
    model: "chat-model",
    enabled: true,
    baseUrl: "https://api.example.com/v1",
    apiKey: "secret-chat-key"
  },
  memoryModel: {
    provider: "openai-compatible",
    model: "memory-model",
    enabled: true,
    baseUrl: "https://api.example.com/v1",
    apiKey: "secret-memory-key"
  },
  taskRunner: {
    provider: "deterministic",
    runners: [],
    sandbox: "workspace-write",
    approvalPolicy: "never",
    timeoutMs: 300000
  },
  proactive: {
    idleThresholdMs: 600000,
    idleCooldownMs: 1800000,
    followUpLookaheadMs: 0,
    pollIntervalMs: 10000
  }
};

describe("updateUserModelProviderSettings", () => {
  it("persists selected model providers and enables external providers", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-model-provider-"));
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify(
        {
          chatModel: {
            apiKey: "persisted-chat-key",
            model: "persisted-chat-model"
          }
        },
        null,
        2
      ),
      "utf8"
    );

    const nextConfig = updateUserModelProviderSettings(userDataPath, baseConfig, {
      role: "chatModel",
      provider: "langchain-openai"
    });
    const userConfig = JSON.parse(readFileSync(join(userDataPath, "config.json"), "utf8")) as {
      chatModel: AppConfig["chatModel"];
    };

    expect(nextConfig.chatModel).toMatchObject({
      provider: "langchain-openai",
      enabled: true,
      model: "persisted-chat-model",
      apiKey: "persisted-chat-key"
    });
    expect(userConfig.chatModel).toMatchObject({
      provider: "langchain-openai",
      enabled: true,
      model: "persisted-chat-model",
      apiKey: "persisted-chat-key"
    });
  });

  it("keeps external providers enabled without deleting connection details", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-model-provider-"));

    const nextConfig = updateUserModelProviderSettings(userDataPath, baseConfig, {
      role: "memoryModel",
      provider: "langchain-openai"
    });
    const userConfig = JSON.parse(readFileSync(join(userDataPath, "config.json"), "utf8")) as {
      memoryModel: AppConfig["memoryModel"];
    };

    expect(nextConfig.memoryModel).toMatchObject({
      provider: "langchain-openai",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
      apiKey: "secret-memory-key"
    });
    expect(userConfig.memoryModel).toMatchObject({
      provider: "langchain-openai",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
      apiKey: "secret-memory-key"
    });
  });
});

describe("updateUserProactiveSettings", () => {
  it("persists proactive idle timing without touching other config details", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-proactive-settings-"));
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify(
        {
          chatModel: {
            apiKey: "persisted-chat-key"
          }
        },
        null,
        2
      ),
      "utf8"
    );

    const nextConfig = updateUserProactiveSettings(userDataPath, baseConfig, {
      idleThresholdMs: 120000
    });
    const userConfig = JSON.parse(readFileSync(join(userDataPath, "config.json"), "utf8")) as {
      proactive: AppConfig["proactive"];
      chatModel: AppConfig["chatModel"];
    };

    expect(nextConfig.proactive).toMatchObject({
      idleThresholdMs: 120000,
      idleCooldownMs: 1800000,
      pollIntervalMs: 10000
    });
    expect(userConfig.proactive).toMatchObject({
      idleThresholdMs: 120000,
      idleCooldownMs: 1800000,
      pollIntervalMs: 10000
    });
    expect(userConfig.chatModel.apiKey).toBe("persisted-chat-key");
  });
});
