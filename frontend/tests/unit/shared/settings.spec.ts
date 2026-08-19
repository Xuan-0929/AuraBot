/**
 * 验证 Renderer 设置投影隐藏密钥且不包含 Task runner。
 *
 * 测试覆盖模型、健康枚举和 proactive 设置的当前公共契约。
 */

import { describe, expect, it } from "vitest";
import {
  MODEL_HEALTH_ERROR_CODES,
  MODEL_HEALTH_STATES,
  MODEL_PROVIDER_OPTIONS,
  createPublicAppConfig
} from "@shared/contracts/settings";
import type { AppConfig } from "@shared/schemas/config";

describe("createPublicAppConfig", () => {
  it("projects only retained non-secret settings", () => {
    const config: AppConfig = {
      storagePath: "/tmp/aurabot.sqlite",
      debugLogging: true,
      gateModel: {
        provider: "openai-compatible",
        model: "gate",
        enabled: true,
        apiKey: "secret"
      },
      chatModel: {
        provider: "openai-compatible",
        model: "chat",
        enabled: true,
        apiKey: "secret"
      },
      memoryModel: {
        provider: "openai-compatible",
        model: "memory",
        enabled: false
      },
      proactive: {
        pollIntervalMs: 10_000,
        followUpLookaheadMs: 0,
        idleThresholdMs: 60_000,
        idleCooldownMs: 120_000
      }
    };

    const publicConfig = createPublicAppConfig(config);

    expect(publicConfig.chatModel.hasApiKey).toBe(true);
    expect(publicConfig).not.toHaveProperty("taskRunner");
    expect(JSON.stringify(publicConfig)).not.toContain("secret");
  });

  it("keeps model option and health enums stable", () => {
    expect(MODEL_PROVIDER_OPTIONS).toEqual(["openai-compatible", "langchain-openai"]);
    expect(MODEL_HEALTH_STATES).toContain("connected");
    expect(MODEL_HEALTH_ERROR_CODES).toContain("network_error");
  });
});
