/**
 * 验证 Memory 管理面只展示 Python owner 的 active items 与公开 jobs，并把忘记/重试
 * 作为显式事件交给页面；空状态和 Backend 错误不再伪装成旧图谱快照。
 */
// @vitest-environment jsdom

import { createApp } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import MemoryDebugContent from "../../../src/features/settings/MemoryDebugContent.vue";

describe("MemoryDebugContent", () => {
  let container: HTMLDivElement;

  beforeEach(() => {
    container = document.createElement("div");
    document.body.append(container);
  });

  afterEach(() => {
    container.remove();
    vi.restoreAllMocks();
  });

  it("renders active Memory and retryable jobs and emits their explicit actions", async () => {
    const onForget = vi.fn();
    const onRetry = vi.fn();
    const app = createApp(MemoryDebugContent, {
      displayLanguage: "en",
      snapshot: {
        items: [{
          memoryId: "memory-1",
          episodeId: "episode-1",
          type: "preference",
          content: "User likes oolong tea",
          createdAt: "2026-08-28T00:00:00.000Z",
          updatedAt: "2026-08-28T00:00:00.000Z"
        }],
        jobs: [{
          jobId: "job-1",
          kind: "extraction",
          state: "failed",
          attemptCount: 4,
          lastError: "provider unavailable",
          updatedAt: "2026-08-28T00:00:01.000Z"
        }]
      },
      onForget,
      onRetry
    });
    app.mount(container);

    expect(container.textContent).toContain("User likes oolong tea");
    expect(container.textContent).toContain("provider unavailable");
    const forget = container.querySelector<HTMLButtonElement>("[data-memory-action='forget']");
    const retry = container.querySelector<HTMLButtonElement>("[data-memory-action='retry']");
    forget?.click();
    retry?.click();
    await Promise.resolve();

    expect(onForget).toHaveBeenCalledWith("memory-1");
    expect(onRetry).toHaveBeenCalledWith("job-1");
    app.unmount();
  });

  it("shows separate empty and Backend-unavailable states", () => {
    const emptyApp = createApp(MemoryDebugContent, {
      displayLanguage: "en",
      snapshot: { items: [], jobs: [] }
    });
    emptyApp.mount(container);
    expect(container.textContent).toContain("No active memories yet.");
    emptyApp.unmount();

    const errorApp = createApp(MemoryDebugContent, {
      displayLanguage: "en",
      error: "Backend unavailable"
    });
    errorApp.mount(container);
    expect(container.textContent).toContain("Backend unavailable");
    errorApp.unmount();
  });
});
