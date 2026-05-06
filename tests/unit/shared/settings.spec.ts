import { describe, expect, it } from "vitest";
import { createPublicAppConfig } from "../../../src/shared/contracts/settings";
import type { AppConfig } from "../../../src/shared/schemas/config";

describe("createPublicAppConfig", () => {
  it("summarizes model config without exposing API keys to the renderer", () => {
    const config: AppConfig = {
      storagePath: "/tmp/aurabot.sqlite",
      debugLogging: true,
      gateModel: {
        provider: "openai-compatible",
        model: "gate-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "secret-gate-key"
      },
      chatModel: {
        provider: "openai-compatible",
        model: "chat-model",
        enabled: true,
        baseUrl: "https://api.example.com/v1",
        apiKey: "secret-chat-key",
        temperature: 0.7
      },
      memoryModel: {
        provider: "local-stub",
        model: "memory-stub",
        enabled: false
      },
      taskRunner: {
        provider: "deterministic",
        runners: [
          {
            id: "codex",
            enabled: true,
            priority: 1,
            command: "/usr/local/bin/codex",
            workingDirectory: "/Users/xuan/project",
            sandbox: "workspace-write",
            approvalPolicy: "never",
            timeoutMs: 300000,
            extraArgs: []
          },
          {
            id: "claude",
            enabled: false,
            priority: 2,
            command: "/opt/homebrew/bin/claude",
            sandbox: "workspace-write",
            approvalPolicy: "never",
            timeoutMs: 300000,
            extraArgs: []
          },
          {
            id: "hermes",
            enabled: true,
            priority: 3,
            command: "hermes",
            sandbox: "workspace-write",
            approvalPolicy: "never",
            timeoutMs: 300000,
            extraArgs: []
          }
        ],
        sandbox: "workspace-write",
        approvalPolicy: "never",
        timeoutMs: 300000
      }
    };

    const publicConfig = createPublicAppConfig(config);

    expect(publicConfig.chatModel).toMatchObject({
      provider: "openai-compatible",
      model: "chat-model",
      enabled: true,
      baseUrl: "https://api.example.com/v1",
      hasApiKey: true,
      temperature: 0.7
    });
    expect(publicConfig.memoryModel.hasApiKey).toBe(false);
    expect(publicConfig.taskRunner.runners).toEqual([
      {
        id: "codex",
        label: "Codex",
        enabled: true,
        priority: 1,
        command: "/usr/local/bin/codex",
        workingDirectory: "/Users/xuan/project"
      },
      {
        id: "claude",
        label: "Claude Code",
        enabled: false,
        priority: 2,
        command: "/opt/homebrew/bin/claude",
        workingDirectory: undefined
      },
      {
        id: "hermes",
        label: "Hermes",
        enabled: true,
        priority: 3,
        command: "hermes",
        workingDirectory: undefined
      }
    ]);
    expect(JSON.stringify(publicConfig)).not.toContain("secret");
  });
});
