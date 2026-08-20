/**
 * 验证 AuraBot Pi Extension 暴露的唯一业务 Tool。
 *
 * 测试以真实 Tool definition 调用 execute，固定 free-text、options、取消与中断的
 * 用户可观察结果；仅以极薄 UI double 替代 RPC 模式下由 Pi 提供的交互边界。
 */

import assert from "node:assert/strict";
import { describe, it, mock } from "node:test";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import registerAuraBotTask from "../aurabot-task/index.ts";

type RegisteredTool = Parameters<ExtensionAPI["registerTool"]>[0];
type UiDouble = {
  input: ReturnType<typeof mock.fn>;
  select: ReturnType<typeof mock.fn>;
};

function registeredTool(): RegisteredTool {
  const tools: RegisteredTool[] = [];
  registerAuraBotTask({
    registerTool(tool: RegisteredTool) {
      tools.push(tool);
    },
  } as unknown as ExtensionAPI);
  assert.equal(tools.length, 1);
  assert.equal(tools[0]?.name, "ask_main_agent");
  return tools[0]!;
}

async function execute(
  tool: RegisteredTool,
  params: { question: string; options?: string[] },
  signal: AbortSignal,
  ui: UiDouble,
) {
  return tool.execute("tool-call-1", params, signal, undefined, { ui } as never);
}

describe("ask_main_agent", () => {
  it("asks a marker-prefixed free-text question and returns the answer", async () => {
    const tool = registeredTool();
    const ui = {
      input: mock.fn(async () => "使用蓝色"),
      select: mock.fn(),
    };

    const signal = new AbortController().signal;
    const result = await execute(
      tool,
      { question: "按钮应该使用什么颜色？" },
      signal,
      ui,
    );

    assert.deepEqual(ui.input.mock.calls[0]?.arguments, [
      "AURABOT_TASK_QUESTION_V1\n按钮应该使用什么颜色？",
      "请输入回答",
      { signal },
    ]);
    assert.equal(ui.select.mock.callCount(), 0);
    assert.deepEqual(result, {
      isError: false,
      content: [{ type: "text", text: "Main Agent answered: 使用蓝色" }],
      details: { answer: "使用蓝色" },
    });
  });

  it("uses select when options are provided", async () => {
    const tool = registeredTool();
    const ui = {
      input: mock.fn(),
      select: mock.fn(async () => "保留"),
    };

    const signal = new AbortController().signal;
    const result = await execute(
      tool,
      { question: "如何处理文件？", options: ["保留", "删除"] },
      signal,
      ui,
    );

    assert.deepEqual(ui.select.mock.calls[0]?.arguments, [
      "AURABOT_TASK_QUESTION_V1\n如何处理文件？",
      ["保留", "删除"],
      { signal },
    ]);
    assert.deepEqual(result, {
      isError: false,
      content: [{ type: "text", text: "Main Agent answered: 保留" }],
      details: { answer: "保留" },
    });
  });

  it("returns an error result when the dialog is cancelled", async () => {
    const tool = registeredTool();
    const ui = { input: mock.fn(async () => undefined), select: mock.fn() };

    const result = await execute(
      tool,
      { question: "继续吗？" },
      new AbortController().signal,
      ui,
    );

    assert.deepEqual(result, {
      isError: true,
      content: [{ type: "text", text: "Main Agent question was cancelled." }],
      details: { cancelled: true },
    });
  });

  it("does not open a dialog when already aborted", async () => {
    const tool = registeredTool();
    const controller = new AbortController();
    controller.abort();
    const ui = { input: mock.fn(), select: mock.fn() };

    const result = await execute(tool, { question: "继续吗？" }, controller.signal, ui);

    assert.equal(ui.input.mock.callCount(), 0);
    assert.equal(ui.select.mock.callCount(), 0);
    assert.deepEqual(result, {
      isError: true,
      content: [{ type: "text", text: "Main Agent question was cancelled." }],
      details: { cancelled: true },
    });
  });
});
