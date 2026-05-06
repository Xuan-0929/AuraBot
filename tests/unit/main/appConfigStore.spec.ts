import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { updateUserModelProviderSettings } from "../../../src/main/appConfigStore";
import type { AppConfig } from "../../../src/shared/schemas/config";

const baseConfig: AppConfig = {
  storagePath: "/tmp/aurabot.sqlite",
  debugLogging: true,
  gateModel: {
    provider: "local-stub",
    model: "gate-stub",
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

  it("switches local fallback providers off without deleting connection details", () => {
    const userDataPath = mkdtempSync(join(tmpdir(), "aurabot-model-provider-"));

    const nextConfig = updateUserModelProviderSettings(userDataPath, baseConfig, {
      role: "memoryModel",
      provider: "local-stub"
    });
    const userConfig = JSON.parse(readFileSync(join(userDataPath, "config.json"), "utf8")) as {
      memoryModel: AppConfig["memoryModel"];
    };

    expect(nextConfig.memoryModel).toMatchObject({
      provider: "local-stub",
      enabled: false,
      baseUrl: "https://api.example.com/v1",
      apiKey: "secret-memory-key"
    });
    expect(userConfig.memoryModel).toMatchObject({
      provider: "local-stub",
      enabled: false,
      baseUrl: "https://api.example.com/v1",
      apiKey: "secret-memory-key"
    });
  });
});
