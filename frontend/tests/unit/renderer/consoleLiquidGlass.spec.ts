import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, test } from "vitest";

const rendererRoot = join(dirname(fileURLToPath(import.meta.url)), "../../../src");

function readRendererSource(path: string) {
  return readFileSync(join(rendererRoot, path), "utf8");
}

describe("console glass architecture", () => {
  test("keeps the console glass CSS-only to avoid LiquidGlass filter artifacts", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const consoleGlassPanel = readRendererSource("features/console/ConsoleGlassPanel.vue");
    const messageList = readRendererSource("features/chat/MessageList.vue");

    expect(consoleWindow).toContain("ConsoleGlassPanel");
    expect(consoleWindow).not.toContain("ConsoleLiquidControl");
    expect(consoleWindow).not.toContain("LiquidGlass");
    expect(consoleGlassPanel).not.toContain('@wxperia/liquid-glass-vue');
    expect(messageList).not.toContain("LiquidGlass");
  });

  test("uses a resizable task rail with an automatic hover preview while keeping the conversation header-free", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const styles = readRendererSource("styles.css");

    expect(consoleWindow).toContain("console-task-sidebar");
    expect(consoleWindow).toContain("console-task-sidebar-resize-handle");
    expect(consoleWindow).toContain("ResizeObserver");
    expect(consoleWindow).toContain("shouldCollapseTaskSidebarFromDrag");
    expect(consoleWindow).toContain("collapseTaskSidebarWithAnimation");
    expect(consoleWindow).toContain('data-task-sidebar-mode');
    expect(consoleWindow).toContain('layout="vertical"');
    expect(consoleWindow).not.toContain("console-header-copy");
    expect(styles).toContain(".console-window-task-sidebar-rail-mode");
    expect(styles).toContain("task-sticky-notes-layout-vertical");
    expect(styles).toContain(`.app-shell-console .console-chat {
  grid-column: 1;
}`);
    expect(styles).toContain(`.app-shell-console .console-task-sidebar-resize-handle {
  display: grid;
  grid-column: 2;`);
    expect(styles).toContain(`.app-shell-console .console-task-sidebar {
  grid-column: 3;
}`);
    expect(styles).toContain(`.app-shell-console .console-window-task-sidebar-rail-mode:not(.console-window-task-sidebar-layout-transitioning) .console-task-sidebar {
  border-color: transparent;
  background: transparent;
  box-shadow: none;
  backdrop-filter: none;
}`);
  });

  test("fades task content while the task rail frame opens and closes", () => {
    const consoleWindow = readRendererSource("features/console/ConsoleWindow.vue");
    const styles = readRendererSource("styles.css");

    expect(consoleWindow).toContain('<Transition name="task-sidebar-preview"');
    expect(consoleWindow).toContain('@after-leave="handleTaskSidebarContentAfterLeave"');
    expect(consoleWindow).toContain("isTaskSidebarClosing");
    expect(consoleWindow).toContain("isTaskSidebarLayoutTransitioning");
    expect(styles).toContain(".task-sidebar-preview-enter-active");
    expect(styles).toContain(".task-sidebar-preview-leave-active");
    expect(styles).toContain(".console-window-task-sidebar-layout-transitioning .console-body");
    expect(styles).toContain(`.console-window-task-sidebar-layout-transitioning .console-task-sidebar-content {
  position: absolute;
  z-index: 1;
  top: 0;
  right: 0;`);
    expect(styles).toContain(`.console-window-task-sidebar-layout-transitioning .console-task-sidebar {
  overflow: hidden;
}`);
    expect(styles).toContain(".console-window-task-sidebar-rail-mode:not(.console-window-task-sidebar-layout-transitioning) .console-task-sidebar");
    expect(styles).toContain(`.task-sidebar-preview-leave-to {
  opacity: 0;
  transform: none;
}`);
  });

  test("keeps console tokens scoped away from pet and settings surfaces", () => {
    const styles = readRendererSource("styles.css");

    expect(styles).toContain(".app-shell-console");
    expect(styles).toContain("--console-bg");
    expect(styles).toMatch(/@media\s+\(prefers-color-scheme:\s*dark\)[\s\S]*--console-bg/);
    expect(styles).toMatch(/\.console-window\s+\.message-user/);
    expect(styles).toContain(".console-window .composer-status-dot");
  });

  test("uses one composer focus treatment with a compact action rail", () => {
    const styles = readRendererSource("styles.css");

    expect(styles).toContain(`.app-shell-console .console-window .composer-writing-surface:focus-within {
  border-color: color-mix(in srgb, var(--accent) 62%, var(--edge-subtle));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 15%, transparent);
}`);
    expect(styles).toContain(`.console-window .composer textarea:focus {
  border: 0;
  box-shadow: none;
}`);
    expect(styles).toContain(`.console-window .composer-action-rail {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  min-height: 34px;
  padding: 1px 2px 0;
}`);
  });

  test("uses a transparent three-layer material system without decorative fog", () => {
    const styles = readRendererSource("styles.css");

    expect(styles).toContain("--surface-primary-glass");
    expect(styles).toContain("--surface-secondary-glass");
    expect(styles).toContain("--surface-raised");
    expect(styles).toContain("/* Liquid Glass V2: final scoped material system */");
    expect(styles).toContain(".app-shell-console .console-window::after");
    expect(styles).toContain("content: none;");
  });

  test("anchors the message rail to the chat surface while keeping individual bubbles bounded", () => {
    const styles = readRendererSource("styles.css");

    expect(styles).toContain(`.app-shell-console .console-window .message-list {
  width: 100%;
  margin: 0;`);
    expect(styles).toContain("max-width: min(620px, 84%);");
  });
});
