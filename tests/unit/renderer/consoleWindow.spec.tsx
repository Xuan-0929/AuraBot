// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ConsoleWindow, type ConsoleWindowProps } from "../../../src/renderer/src/features/console/ConsoleWindow";
import type { Task } from "../../../src/shared/schemas/task";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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

    expect(container.textContent).toContain("AuraBot 控制台");
    expect(container.textContent).toContain("设置");
    expect(container.querySelector("textarea")?.getAttribute("placeholder")).toBe("说点什么");
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
    expect(stickyNotes?.textContent).toContain("周报");
    expect(stickyNotes?.textContent).toContain("读书笔记");
    expect(stickyNotes?.textContent).toContain("海报");
    expect(stickyNotes?.textContent).not.toContain("会议纪要");
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
