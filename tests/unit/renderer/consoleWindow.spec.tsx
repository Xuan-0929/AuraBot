// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleWindow, type ConsoleWindowProps } from "../../../src/renderer/src/features/console/ConsoleWindow";
import type { Task } from "../../../src/shared/schemas/task";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const stylesheetText = readFileSync(resolve(process.cwd(), "src/renderer/src/styles.css"), "utf8");

const baseProps: ConsoleWindowProps = {
  agentStatus: "idle",
  messages: [],
  tasks: [],
  petSettings: {
    clickThroughPet: false,
    displayLanguage: "zh"
  },
  appConfig: undefined,
  onUpdatePetSettings: vi.fn(async () => undefined),
  onOpenSettings: vi.fn(async () => undefined),
  onSend: vi.fn(async () => undefined),
  onCancelTask: vi.fn(async () => undefined),
  onDeleteTask: vi.fn(async () => undefined)
};

describe("ConsoleWindow localization", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => {
      root.unmount();
    });
    container.remove();
    vi.restoreAllMocks();
  });

  it("uses Chinese text across the console shell when Chinese is selected", async () => {
    await act(async () => {
      root.render(<ConsoleWindow {...baseProps} />);
    });

    expect(container.textContent).toContain("Aura 仪表盘");
    expect(container.textContent).toContain("任务便签");
    const viewport = container.querySelector<HTMLElement>(".console-task-viewport");
    const panelMeta = container.querySelector<HTMLElement>(".console-task-panel-meta");
    expect(panelMeta).not.toBeNull();
    expect(viewport).not.toBeNull();
    expect(panelMeta?.textContent).toContain("空闲中");
    expect(viewport?.textContent).not.toContain("空闲中");
    expect(container.querySelector(".console-task-viewport-empty-card")).toBeNull();
    expect(container.querySelector("textarea")?.getAttribute("placeholder")).toBe("说点什么");
    expect(container.querySelector('[aria-label="打开设置菜单"]')).toBeNull();
    expect(container.textContent).not.toContain("Chat here when");
    expect(container.textContent).not.toContain("Ready when you are");
    expect(container.textContent).not.toContain("Say something");
  });

  it("uses the A1 Chinese copy in the console shell", async () => {
    await act(async () => {
      root.render(<ConsoleWindow {...baseProps} />);
    });

    expect(container.textContent).toContain("想认真聊的时候，我们就在这里慢慢说。");
    expect(container.textContent).toContain("我在，今天想先从哪件事开始？");
    expect(container.textContent).not.toContain("Memory Debug");
    expect(container.querySelector(".console-header-title-row .status-bar")?.textContent).toContain("空闲中");
  });

  it("replaces the old task overlay with console sticky notes beside the chat stream", async () => {
    const tasks = [
      makeTask({ id: "task-running", title: "帮我整理周报。", status: "running", updatedAt: "2026-05-07T09:00:00.000Z" }),
      makeTask({ id: "task-completed", title: "帮我写读书笔记。", status: "completed", updatedAt: "2026-05-07T08:00:00.000Z" }),
      makeTask({ id: "task-failed", title: "帮我生成海报。", status: "failed", updatedAt: "2026-05-07T07:00:00.000Z" }),
      makeTask({ id: "task-old", title: "帮我整理会议纪要。", status: "completed", updatedAt: "2026-05-07T06:00:00.000Z" })
    ];

    await act(async () => {
      root.render(<ConsoleWindow {...baseProps} tasks={tasks} />);
    });

    expect(container.querySelector(".task-status-overlay")).toBeNull();

    const stickyNotes = container.querySelector(".task-sticky-notes-console");
    expect(stickyNotes).not.toBeNull();
    expect(stickyNotes?.closest(".console-task-viewport")).not.toBeNull();
    expect(stickyNotes?.closest(".console-chat")).toBeNull();
    expect(stickyNotes?.textContent).toContain("周报");
    expect(stickyNotes?.textContent).toContain("读书笔记");
    expect(stickyNotes?.textContent).toContain("海报");
    expect(stickyNotes?.textContent).toContain("会议纪要");
  });

  it("keeps a fixed tiny task viewport in the header's top-right corner without changing the chat body width", () => {
    expect(stylesheetText).toMatch(/\.console-header\s*{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(260px,\s*0\.82fr\)\s*minmax\(340px,\s*1\.18fr\);[^}]*align-items:\s*stretch;[^}]*gap:\s*6px;[^}]*min-height:\s*144px;/s);
    expect(stylesheetText).toMatch(/\.console-header-copy\s*{[^}]*display:\s*flex;[^}]*justify-content:\s*center;[^}]*min-height:\s*100%;/s);
    expect(stylesheetText).toMatch(/\.console-task-sticky-layer\s*{[^}]*display:\s*flex;[^}]*justify-content:\s*center;[^}]*min-height:\s*100%;/s);
    expect(stylesheetText).toMatch(/\.console-task-panel-meta\s*{[^}]*display:\s*flex;[^}]*justify-content:\s*space-between;/s);
    expect(stylesheetText).toMatch(/\.console-task-panel-meta strong\s*{[^}]*font-size:\s*15px;/s);
    expect(stylesheetText).toMatch(/\.console-task-panel-meta span\s*{[^}]*font-size:\s*13px;/s);
    expect(stylesheetText).toMatch(/\.console-task-viewport\s*{[^}]*height:\s*96px;[^}]*padding:\s*10px;[^}]*width:\s*100%;[^}]*min-width:\s*0;/s);
    expect(stylesheetText).toMatch(/\.console-task-viewport-list\s*{[^}]*height:\s*100%;[^}]*overflow-x:\s*auto;[^}]*overflow-y:\s*hidden;/s);
    expect(stylesheetText).toMatch(/\.console-body\s*{[^}]*display:\s*flex;[^}]*min-height:\s*0;/s);
    expect(stylesheetText).toMatch(/\.task-sticky-notes-console\s*{[^}]*display:\s*flex;[^}]*align-items:\s*center;[^}]*width:\s*max-content;[^}]*min-width:\s*max-content;[^}]*height:\s*100%;/s);
    expect(stylesheetText).toMatch(/\.task-sticky-notes-console\s+\.task-sticky-note\s*{[^}]*display:\s*flex;[^}]*flex-direction:\s*row;[^}]*width:\s*138px;[^}]*height:\s*90%;/s);
    expect(stylesheetText).toMatch(/\.task-sticky-notes-console\s+\.task-sticky-note-actions\s*{[^}]*align-self:\s*center;[^}]*justify-content:\s*flex-end;/s);
  });
});

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: "task-1",
    profileId: "profile-1",
    parentSessionId: "session-1",
    sessionId: "task-session-1",
    kind: "result_output",
    title: "帮我写一份 GitHub 周报。",
    status: "queued",
    latestProgress: "Task queued from main chat.",
    createdAt: "2026-05-05T00:00:00.000Z",
    updatedAt: "2026-05-05T00:00:02.000Z",
    ...overrides
  };
}
