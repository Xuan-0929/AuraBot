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
    expect(JSON.stringify(publicConfig)).not.toContain("secret");
  });
});
