/**
 * 验证用户设置写入只更新模型与非 Task proactive 字段。
 *
 * 测试确认历史 taskRunner JSON 原样保留但不进入当前 AppConfig，满足“不主动重写旧
 * 配置、同时停止读取”的迁移约束。
 */

import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { loadConfig } from "@core/bootstrap/loadConfig";
import {
  updateUserModelProviderSettings,
  updateUserProactiveSettings
} from "../../../src/main/appConfigStore";

const roots: string[] = [];
afterEach(() => roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true })));

describe("appConfigStore", () => {
  it("updates model provider without rewriting a legacy taskRunner field", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-config-store-"));
    roots.push(root);
    const path = join(root, "config.json");
    writeFileSync(path, JSON.stringify({ taskRunner: { provider: "codex" } }));
    const next = updateUserModelProviderSettings(root, loadConfig(root), {
      role: "chatModel",
      provider: "langchain-openai"
    });
    const persisted = JSON.parse(readFileSync(path, "utf8"));

    expect(next.chatModel.provider).toBe("langchain-openai");
    expect(next).not.toHaveProperty("taskRunner");
    expect(persisted.taskRunner).toEqual({ provider: "codex" });
  });

  it("updates retained proactive timing", () => {
    const root = mkdtempSync(join(tmpdir(), "aurabot-config-store-"));
    roots.push(root);
    expect(
      updateUserProactiveSettings(root, loadConfig(root), {
        idleThresholdMs: 42_000
      }).proactive.idleThresholdMs
    ).toBe(42_000);
  });
});
