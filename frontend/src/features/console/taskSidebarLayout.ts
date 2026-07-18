export const DEFAULT_TASK_SIDEBAR_WIDTH = 262;
export const TASK_SIDEBAR_MIN_WIDTH = 220;
export const TASK_SIDEBAR_MAX_WIDTH = 420;
export const TASK_SIDEBAR_COLLAPSE_THRESHOLD = TASK_SIDEBAR_MIN_WIDTH / 2;
export const TASK_SIDEBAR_RAIL_WIDTH = 44;
export const AUTO_COLLAPSE_CHAT_MIN_WIDTH = 640;
export const TASK_SIDEBAR_GAP = 14;

const STORAGE_KEY = "aurabot.console.task-sidebar-layout.v1";

export interface TaskSidebarStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface StoredTaskSidebarLayout {
  version: 1;
  width: number;
}

export function clampTaskSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) {
    return DEFAULT_TASK_SIDEBAR_WIDTH;
  }
  return Math.min(TASK_SIDEBAR_MAX_WIDTH, Math.max(TASK_SIDEBAR_MIN_WIDTH, Math.round(width)));
}

export function calculateTaskSidebarTargetWidth(input: { startWidth: number; startClientX: number; clientX: number }): number {
  return Math.round(input.startWidth - (input.clientX - input.startClientX));
}

export function calculateTaskSidebarWidth(input: { startWidth: number; startClientX: number; clientX: number }): number {
  return clampTaskSidebarWidth(calculateTaskSidebarTargetWidth(input));
}

export function shouldCollapseTaskSidebarFromDrag(targetWidth: number): boolean {
  return targetWidth <= TASK_SIDEBAR_COLLAPSE_THRESHOLD;
}

export function shouldAutoCollapseTaskSidebar(consoleBodyWidth: number, sidebarWidth: number): boolean {
  return consoleBodyWidth < AUTO_COLLAPSE_CHAT_MIN_WIDTH + clampTaskSidebarWidth(sidebarWidth) + TASK_SIDEBAR_GAP;
}

export function readStoredTaskSidebarWidth(storage: Pick<TaskSidebarStorage, "getItem">): number {
  try {
    const stored = storage.getItem(STORAGE_KEY);
    if (!stored) {
      return DEFAULT_TASK_SIDEBAR_WIDTH;
    }
    const parsed = JSON.parse(stored) as Partial<StoredTaskSidebarLayout>;
    if (parsed.version !== 1 || typeof parsed.width !== "number") {
      return DEFAULT_TASK_SIDEBAR_WIDTH;
    }
    return clampTaskSidebarWidth(parsed.width);
  } catch {
    return DEFAULT_TASK_SIDEBAR_WIDTH;
  }
}

export function writeStoredTaskSidebarWidth(storage: Pick<TaskSidebarStorage, "setItem">, width: number): void {
  const payload: StoredTaskSidebarLayout = { version: 1, width: clampTaskSidebarWidth(width) };
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Layout preference persistence must never prevent the console from working.
  }
}
