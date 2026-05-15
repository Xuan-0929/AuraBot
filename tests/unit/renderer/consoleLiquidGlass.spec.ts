import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const repoRoot = join(import.meta.dirname, "../../..");

function readRendererSource(path: string) {
  return readFileSync(join(repoRoot, "src/renderer/src", path), "utf8");
}

describe("console liquid glass architecture", () => {
  test("keeps LiquidGlass isolated to small console controls", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const consoleLiquidControl = readRendererSource("features/console/ConsoleLiquidControl.vue");
    const consoleGlassPanel = readRendererSource("features/console/ConsoleGlassPanel.vue");
    const messageList = readRendererSource("features/chat/MessageList.vue");

    expect(consoleWindow).toContain("ConsoleLiquidControl");
    expect(consoleWindow).toContain("ConsoleGlassPanel");
    expect(consoleLiquidControl).toContain('@wxperia/liquid-glass-vue');
    expect(consoleGlassPanel).not.toContain('@wxperia/liquid-glass-vue');
    expect(messageList).not.toContain("LiquidGlass");
  });

  test("does not wrap large task or composer surfaces in LiquidGlass", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");

    expect(consoleWindow).toMatch(/<ConsoleGlassPanel[^>]*class="console-task-glass"/);
    expect(consoleWindow).toMatch(/<ConsoleGlassPanel[^>]*class="console-composer-glass"/);
    expect(consoleWindow).not.toMatch(/<ConsoleLiquidControl[^>]*class="console-task-glass"/);
    expect(consoleWindow).not.toMatch(/<ConsoleLiquidControl[^>]*class="console-composer-glass"/);
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
