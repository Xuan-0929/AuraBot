import { describe, expect, test } from "vitest";
import {
  AUTO_COLLAPSE_CHAT_MIN_WIDTH,
  DEFAULT_TASK_SIDEBAR_WIDTH,
  TASK_SIDEBAR_COLLAPSE_THRESHOLD,
  TASK_SIDEBAR_MAX_WIDTH,
  TASK_SIDEBAR_MIN_WIDTH,
  calculateTaskSidebarTargetWidth,
  calculateTaskSidebarWidth,
  clampTaskSidebarWidth,
  readStoredTaskSidebarWidth,
  shouldCollapseTaskSidebarFromDrag,
  shouldAutoCollapseTaskSidebar
} from "../../../src/features/console/taskSidebarLayout";

describe("task sidebar layout", () => {
  test("clamps a persisted task sidebar width to the supported desktop range", () => {
    expect(clampTaskSidebarWidth(TASK_SIDEBAR_MIN_WIDTH - 1)).toBe(TASK_SIDEBAR_MIN_WIDTH);
    expect(clampTaskSidebarWidth(TASK_SIDEBAR_MAX_WIDTH + 1)).toBe(TASK_SIDEBAR_MAX_WIDTH);
    expect(clampTaskSidebarWidth(DEFAULT_TASK_SIDEBAR_WIDTH)).toBe(DEFAULT_TASK_SIDEBAR_WIDTH);
  });

  test("gives the conversation its minimum width before automatically collapsing task notes", () => {
    const requiredWidth = AUTO_COLLAPSE_CHAT_MIN_WIDTH + DEFAULT_TASK_SIDEBAR_WIDTH + 14;

    expect(shouldAutoCollapseTaskSidebar(requiredWidth, DEFAULT_TASK_SIDEBAR_WIDTH)).toBe(false);
    expect(shouldAutoCollapseTaskSidebar(requiredWidth - 1, DEFAULT_TASK_SIDEBAR_WIDTH)).toBe(true);
  });

  test("uses the left sidebar edge so dragging right narrows task notes", () => {
    expect(calculateTaskSidebarWidth({ startWidth: 300, startClientX: 500, clientX: 540 })).toBe(260);
    expect(calculateTaskSidebarWidth({ startWidth: 300, startClientX: 500, clientX: 420 })).toBe(380);
  });

  test("holds at the minimum width before a drag reaches the collapse threshold", () => {
    const input = { startWidth: 300, startClientX: 500, clientX: 760 };

    expect(calculateTaskSidebarTargetWidth(input)).toBe(40);
    expect(calculateTaskSidebarWidth(input)).toBe(TASK_SIDEBAR_MIN_WIDTH);
    expect(shouldCollapseTaskSidebarFromDrag(TASK_SIDEBAR_COLLAPSE_THRESHOLD + 1)).toBe(false);
    expect(shouldCollapseTaskSidebarFromDrag(TASK_SIDEBAR_COLLAPSE_THRESHOLD)).toBe(true);
  });

  test("falls back to the default width when the saved layout is invalid", () => {
    const storage = { getItem: () => '{"version":1,"width":"wide"}' };

    expect(readStoredTaskSidebarWidth(storage)).toBe(DEFAULT_TASK_SIDEBAR_WIDTH);
  });
});
