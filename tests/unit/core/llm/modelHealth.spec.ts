import { describe, expect, it, vi } from "vitest";
import { createModelHealthService } from "../../../../src/core/llm/modelHealth";
import type { AppConfig } from "../../../../src/shared/schemas/config";

const baseConfig: AppConfig = {
  storagePath: "/tmp/aurabot.sqlite",
  debugLogging: true,
  gateModel: {
    provider: "openai-compatible",
    model: "gate-model",
    enabled: true,
    baseUrl: "https://api.example.com/v1",
    apiKey: "gate-key"
  },
  chatModel: {
    provider: "openai-compatible",
    model: "chat-model",
    enabled: true,
    baseUrl: "https://api.example.com/v1",
    apiKey: "chat-key"
  },
  memoryModel: {
    provider: "langchain-openai",
    model: "memory-model",
    enabled: true,
    baseUrl: "https://api.example.com/v1",
    apiKey: "memory-key"
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

describe("createModelHealthService", () => {
  it("marks missing model configuration as not configured during static inspection", () => {
    const service = createModelHealthService({
      now: () => new Date("2026-05-06T12:00:00.000Z")
    });

    const health = service.inspectRole("gateModel", {
      ...baseConfig.gateModel,
      enabled: false,
      apiKey: undefined
    });

    expect(health).toMatchObject({
      role: "gateModel",
      configured: false,
      state: "not_configured",
      hasApiKey: false,
      lastErrorCode: "missing_configuration"
    });
  });

  it("marks configured roles as connected after a successful probe", async () => {
    const probeOpenAICompatible = vi.fn(async () => undefined);
    const service = createModelHealthService({
      now: () => new Date("2026-05-06T12:34:56.000Z"),
      probeOpenAICompatible
    });

    const health = await service.testRoleConnection("chatModel", baseConfig.chatModel);

    expect(probeOpenAICompatible).toHaveBeenCalledTimes(1);
    expect(health).toMatchObject({
      role: "chatModel",
      configured: true,
      state: "connected",
      lastCheckedAt: "2026-05-06T12:34:56.000Z"
    });
  });

  it("routes langchain memory probes through the structured memory path", async () => {
    const probeLangChainStructuredMemory = vi.fn(async () => undefined);
    const service = createModelHealthService({
      now: () => new Date("2026-05-06T12:34:56.000Z"),
      probeLangChainStructuredMemory
    });

    const health = await service.testRoleConnection("memoryModel", baseConfig.memoryModel);

    expect(probeLangChainStructuredMemory).toHaveBeenCalledTimes(1);
    expect(health.state).toBe("connected");
  });

  it("normalizes authentication failures into a stable public error state", async () => {
    const service = createModelHealthService({
      now: () => new Date("2026-05-06T12:34:56.000Z"),
      probeOpenAICompatible: async () => {
        throw new Error("OpenAI-compatible request failed with status 401");
      }
    });

    const health = await service.testRoleConnection("gateModel", baseConfig.gateModel);

    expect(health).toMatchObject({
      role: "gateModel",
      state: "error",
      lastErrorCode: "authentication_failed"
    });
  });
});
