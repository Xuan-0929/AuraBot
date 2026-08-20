/**
 * 验证可分发 Pi Package 的 manifest 与入口文件边界。
 *
 * 测试从磁盘读取真实 package.json，并确保 Pi 能通过唯一 extension 入口与 prompts
 * 目录加载本包；它不会测试 npm 自身的打包实现。
 */

import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { describe, it } from "node:test";
import { fileURLToPath } from "node:url";

const packageRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");

describe("Pi package manifest", () => {
  it("declares the one approved extension and prompt directory", async () => {
    const manifest = JSON.parse(
      await readFile(resolve(packageRoot, "package.json"), "utf8"),
    );

    assert.equal(manifest.name, "@scpz24/pi-aurabot-task");
    assert.equal(manifest.private, false);
    assert.deepEqual(manifest.pi, {
      extensions: ["./aurabot-task/index.ts"],
      prompts: ["./prompts"],
    });
    await access(resolve(packageRoot, "aurabot-task/index.ts"));
    await access(resolve(packageRoot, "prompts/task-system.md"));
  });
});
