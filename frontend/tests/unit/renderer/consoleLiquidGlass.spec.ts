/**
 * Console 结构测试：确认主窗口仅组合聊天与 CSS glass 控制面板。
 * 断言显式排除已删除的 Task 侧栏，同时保留 surface-scoped 视觉 token。
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const rendererRoot = join(dirname(fileURLToPath(import.meta.url)), "../../../src");
const readRendererSource = (path: string) => readFileSync(join(rendererRoot, path), "utf8");

describe("console glass architecture", () => {
  test("keeps one chat surface and no Task rail", () => {
    const source = readRendererSource("features/console/ConsoleWindow.vue");
    expect(source).toContain("MessageList");
    expect(source).toContain("ConsoleGlassPanel");
    expect(source).not.toContain("TaskStickyNotes");
    expect(source).not.toContain("console-task-sidebar");
  });

  test("keeps Console material tokens scoped from pet and settings", () => {
    const styles = readRendererSource("styles.css");
    expect(styles).toContain(".app-shell-console");
    expect(styles).toContain("--console-bg");
    expect(styles).toContain(".console-window .composer-status-dot");
  });
});
