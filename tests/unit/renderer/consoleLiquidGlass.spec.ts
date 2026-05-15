import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const repoRoot = join(import.meta.dirname, "../../..");

function readRendererSource(path: string) {
  return readFileSync(join(repoRoot, "src/renderer/src", path), "utf8");
}

describe("console glass architecture", () => {
  test("keeps console glass CSS-only to avoid LiquidGlass filter artifacts", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const consoleGlassPanel = readRendererSource("features/console/ConsoleGlassPanel.vue");
    const messageList = readRendererSource("features/chat/MessageList.vue");

    expect(consoleWindow).toContain("ConsoleGlassPanel");
    expect(consoleWindow).not.toContain("ConsoleLiquidControl");
    expect(consoleWindow).not.toContain("LiquidGlass");
    expect(consoleGlassPanel).not.toContain('@wxperia/liquid-glass-vue');
    expect(messageList).not.toContain("LiquidGlass");
  });

  test("uses CSS glass panels for status, task, and composer surfaces", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");

    expect(consoleWindow).toMatch(/<ConsoleGlassPanel[^>]*class="console-status-glass"/);
    expect(consoleWindow).toMatch(/<ConsoleGlassPanel[^>]*class="console-task-glass"/);
    expect(consoleWindow).toMatch(/<ConsoleGlassPanel[^>]*class="console-composer-glass"/);
  });

  test("keeps Apple-style console tokens scoped away from pet and settings surfaces", () => {
    const styles = readRendererSource("styles.css");

    expect(styles).toContain(".app-shell-console");
    expect(styles).toContain("--console-bg");
    expect(styles).toMatch(/@media\s+\(prefers-color-scheme:\s*dark\)[\s\S]*--console-bg/);
    expect(styles).toMatch(/\.console-window\s+\.message-user/);
    expect(styles).toMatch(/\.console-window\s+\.task-sticky-notes-console/);
  });
});
