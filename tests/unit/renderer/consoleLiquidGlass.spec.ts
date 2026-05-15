import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const repoRoot = join(import.meta.dirname, "../../..");

function readRendererSource(path: string) {
  return readFileSync(join(repoRoot, "src/renderer/src", path), "utf8");
}

describe("console liquid glass architecture", () => {
  test("centralizes package usage in a console-only glass wrapper", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const consoleGlass = readRendererSource("features/console/ConsoleGlass.vue");
    const messageList = readRendererSource("features/chat/MessageList.vue");

    expect(consoleWindow).toContain("ConsoleGlass");
    expect(consoleGlass).toContain('@wxperia/liquid-glass-vue');
    expect(messageList).not.toContain("LiquidGlass");
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
