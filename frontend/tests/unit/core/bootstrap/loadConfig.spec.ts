/**
 * 验证 Electron 配置只合并保留的模型与主动能力字段。
 *
 * 测试使用临时 JSON 和环境变量，确认旧 taskRunner 与 runner 环境变量被忽略且不会
 * 进入 AppConfig。
 */

import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "@core/bootstrap/loadConfig";

const roots: string[] = [];
afterEach(() => {
  roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true }));
  delete process.env.AURABOT_CHAT_MODEL_NAME;
  delete process.env.AURABOT_TASK_RUNNER_PROVIDER;
  delete process.env.AURABOT_CODEX_COMMAND;
});

describe("loadConfig", () => {
  it("loads retained defaults and model overrides", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-config-"));
    roots.push(root);
    writeFileSync(
      join(root, "config.json"),
      JSON.stringify({
        chatModel: { model: "configured-chat" },
        proactive: { idleThresholdMs: 12345 }
      })
    );

    const config = loadConfig(root);

    expect(config.chatModel.model).toBe("configured-chat");
    expect(config.proactive.idleThresholdMs).toBe(12345);
    expect(config.storagePath).toBe(join(root, "aurabot.sqlite"));
  });

  it("ignores legacy runner JSON and environment fields", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-config-"));
    roots.push(root);
    writeFileSync(
      join(root, "config.json"),
      JSON.stringify({
        taskRunner: { provider: "codex", codexCommand: "/legacy/codex" }
      })
    );
    process.env.AURABOT_TASK_RUNNER_PROVIDER = "codex";
    process.env.AURABOT_CODEX_COMMAND = "/env/codex";

    const config = loadConfig(root);

    expect(config).not.toHaveProperty("taskRunner");
  });

  it("applies retained model environment overrides", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-config-"));
    roots.push(root);
    process.env.AURABOT_CHAT_MODEL_NAME = "env-chat";

    expect(loadConfig(root).chatModel.model).toBe("env-chat");
  });
});
