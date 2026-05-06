import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "../../../../src/core/bootstrap/loadConfig";

const tempDirs: string[] = [];
const originalEnvironment = { ...process.env };

afterEach(() => {
  process.env = { ...originalEnvironment };

  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { force: true, recursive: true });
  }
});

function createUserDataDir(): string {
  const dir = mkdtempSync(join(tmpdir(), "aurabot-config-"));
  tempDirs.push(dir);

  return dir;
}

describe("loadConfig", () => {
  it("defaults model roles to API providers without enabling local fallback", () => {
    const userDataPath = createUserDataDir();
    const config = loadConfig(userDataPath);

    expect(config.gateModel).toMatchObject({
      provider: "openai-compatible",
      model: "gate-model",
      enabled: false
    });
    expect(config.chatModel).toMatchObject({
      provider: "openai-compatible",
      model: "chat-model",
      enabled: false
    });
    expect(config.memoryModel).toMatchObject({
      provider: "openai-compatible",
      model: "memory-model",
      enabled: false
    });
    expect(config.taskRunner).toMatchObject({
      provider: "deterministic",
      sandbox: "workspace-write",
      approvalPolicy: "never"
    });
    expect(config.proactive).toMatchObject({
      pollIntervalMs: 10000,
      idleThresholdMs: 600000,
      idleCooldownMs: 1800000
    });
    expect(config.storagePath).toBe(join(userDataPath, "aurabot.sqlite"));
  });

  it("loads proactive trigger timing from config", () => {
    const userDataPath = createUserDataDir();
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify({
        proactive: {
          idleThresholdMs: 120000,
          idleCooldownMs: 240000,
          pollIntervalMs: 15000
        }
      }),
      "utf8"
    );

    const config = loadConfig(userDataPath);

    expect(config.proactive).toMatchObject({
      idleThresholdMs: 120000,
      idleCooldownMs: 240000,
      pollIntervalMs: 15000
    });
  });

  it("loads separate lightweight gate and stronger chat model roles from config", () => {
    const userDataPath = createUserDataDir();
    writeFileSync(
      join(userDataPath, "aurabot.config.json"),
      JSON.stringify({
        gateModel: {
          provider: "openai-compatible",
          baseUrl: "https://api.example.com/v1",
          apiKey: "gate-key",
          model: "fast-gate-model",
          enabled: true,
          temperature: 0
        },
        chatModel: {
          provider: "openai-compatible",
          baseUrl: "https://api.example.com/v1",
          apiKey: "chat-key",
          model: "strong-chat-model",
          enabled: true,
          temperature: 0.7
        },
        memoryModel: {
          provider: "openai-compatible",
          baseUrl: "https://api.example.com/v1",
          apiKey: "memory-key",
          model: "structured-memory-model",
          enabled: true,
          temperature: 0,
          timeoutMs: 12000
        }
      }),
      "utf8"
    );

    const config = loadConfig(userDataPath);

    expect(config.gateModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "gate-key",
      model: "fast-gate-model",
      enabled: true,
      temperature: 0
    });
    expect(config.chatModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "chat-key",
      model: "strong-chat-model",
      enabled: true,
      temperature: 0.7
    });
    expect(config.memoryModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "memory-key",
      model: "structured-memory-model",
      enabled: true,
      temperature: 0,
      timeoutMs: 12000
    });
  });

  it("also accepts config.json in the app user data directory", () => {
    const userDataPath = createUserDataDir();
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify({
        gateModel: {
          provider: "openai-compatible",
          baseUrl: "https://api.example.com/v1",
          apiKey: "gate-key",
          model: "fast-gate-model",
          enabled: true
        }
      }),
      "utf8"
    );

    const config = loadConfig(userDataPath);

    expect(config.gateModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "gate-key",
      model: "fast-gate-model",
      enabled: true
    });
  });

  it("allows environment variables to override model roles for clean dev launches", () => {
    const userDataPath = createUserDataDir();
    process.env.AURABOT_MODEL_PROVIDER = "openai-compatible";
    process.env.AURABOT_MODEL_BASE_URL = "https://api.example.com/v1";
    process.env.AURABOT_MODEL_API_KEY = "shared-key";
    process.env.AURABOT_GATE_MODEL_ENABLED = "1";
    process.env.AURABOT_GATE_MODEL_NAME = "fast-gate-model";
    process.env.AURABOT_CHAT_MODEL_ENABLED = "true";
    process.env.AURABOT_CHAT_MODEL_NAME = "strong-chat-model";
    process.env.AURABOT_CHAT_MODEL_TEMPERATURE = "0.8";
    process.env.AURABOT_MEMORY_MODEL_ENABLED = "true";
    process.env.AURABOT_MEMORY_MODEL_NAME = "structured-memory-model";
    process.env.AURABOT_MEMORY_MODEL_TIMEOUT_MS = "12000";

    const config = loadConfig(userDataPath);

    expect(config.gateModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "shared-key",
      model: "fast-gate-model",
      enabled: true
    });
    expect(config.chatModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "shared-key",
      model: "strong-chat-model",
      enabled: true,
      temperature: 0.8
    });
    expect(config.memoryModel).toMatchObject({
      provider: "openai-compatible",
      baseUrl: "https://api.example.com/v1",
      apiKey: "shared-key",
      model: "structured-memory-model",
      enabled: true,
      timeoutMs: 12000
    });
  });

  it("loads a Codex task runner config from config and environment overrides", () => {
    const userDataPath = createUserDataDir();
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify({
        taskRunner: {
          provider: "codex",
          codexCommand: "/usr/local/bin/codex",
          workingDirectory: "/Users/xuan/project",
          sandbox: "read-only",
          approvalPolicy: "on-request",
          timeoutMs: 120000
        }
      }),
      "utf8"
    );
    process.env.AURABOT_CODEX_MODEL = "gpt-5.4";
    process.env.AURABOT_CODEX_SANDBOX = "workspace-write";
    process.env.AURABOT_CODEX_APPROVAL_POLICY = "never";

    const config = loadConfig(userDataPath);

    expect(config.taskRunner).toMatchObject({
      provider: "codex",
      codexCommand: "/usr/local/bin/codex",
      workingDirectory: "/Users/xuan/project",
      model: "gpt-5.4",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 120000
    });
    expect(config.taskRunner.runners.find((runner) => runner.id === "codex")).toMatchObject({
      enabled: true,
      priority: 1,
      command: "/usr/local/bin/codex",
      workingDirectory: "/Users/xuan/project",
      model: "gpt-5.4",
      sandbox: "workspace-write",
      approvalPolicy: "never",
      timeoutMs: 120000
    });
  });

  it("loads task CLI switches and priority order for Codex, Claude Code, and Hermes", () => {
    const userDataPath = createUserDataDir();
    writeFileSync(
      join(userDataPath, "config.json"),
      JSON.stringify({
        taskRunner: {
          provider: "cli",
          runners: [
            {
              id: "claude",
              enabled: true,
              priority: 1,
              command: "/opt/homebrew/bin/claude",
              model: "sonnet",
              approvalPolicy: "never"
            },
            {
              id: "codex",
              enabled: true,
              priority: 2,
              command: "/Applications/Codex.app/Contents/Resources/codex",
              sandbox: "workspace-write"
            },
            {
              id: "hermes",
              enabled: false,
              priority: 3,
              command: "hermes"
            }
          ]
        }
      }),
      "utf8"
    );

    const config = loadConfig(userDataPath);

    expect(config.taskRunner.provider).toBe("cli");
    expect(config.taskRunner.runners.map((runner) => ({
      id: runner.id,
      enabled: runner.enabled,
      priority: runner.priority,
      command: runner.command
    }))).toEqual([
      {
        id: "codex",
        enabled: true,
        priority: 2,
        command: "/Applications/Codex.app/Contents/Resources/codex"
      },
      {
        id: "claude",
        enabled: true,
        priority: 1,
        command: "/opt/homebrew/bin/claude"
      },
      {
        id: "hermes",
        enabled: false,
        priority: 3,
        command: "hermes"
      }
    ]);
  });
});
