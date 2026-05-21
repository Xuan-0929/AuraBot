import type { PetSettings } from "@aurabot/shared/schemas/petSettings";
import type { Task } from "@aurabot/shared/schemas/task";
import { taskProgressLabel } from "../../i18n";

export function summarizeTaskTitle(title: string): string {
  const cleaned = title
    .trim()
    .replace(/[。.!！]*$/u, "")
    .replace(/^(请|麻烦)\s*/u, "")
    .replace(
      /^(帮我把|帮我做一下|帮我写一下|帮我整理一下|帮我生成一下|帮我做|帮我写|帮我整理|帮我生成|帮我|做一下|写一下|整理一下|生成一下|做|写|整理|生成)\s*/u,
      ""
    )
    .replace(/^(一份|一个|一篇|一版|这份|这个|一下)\s*/u, "")
    .trim();

  return cleaned || title;
}

export function summarizeTaskMeta(task: Task, displayLanguage: PetSettings["displayLanguage"]): string {
  return taskProgressLabel(displayLanguage, task);
}

export function pickVisiblePetTasks(tasks: Task[]): Task[] {
  return tasks.filter((task) => isStickyTaskActive(task.status)).slice(0, 2);
}

export function pickConsoleStickyTasks(tasks: Task[]): Task[] {
  const activeTasks = tasks
    .filter((task) => isStickyTaskActive(task.status))
    .sort(compareStickyTasksByUpdatedAt);

  const settledTasks = tasks
    .filter((task) => isStickyTaskSettled(task.status))
    .sort(compareStickyTasksByUpdatedAt);

  return [...activeTasks, ...settledTasks];
}

export function isStickyTaskActive(status: Task["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

export function isStickyTaskSettled(status: Task["status"]): boolean {
  return status === "completed" || status === "failed" || status === "interrupted";
}

export function formatTaskDuration(durationMs: number | undefined): string {
  if (typeof durationMs !== "number" || !Number.isFinite(durationMs)) {
    return "—";
  }

  if (durationMs < 1000) {
    return `${Math.max(0, Math.round(durationMs))}ms`;
  }

  const totalSeconds = Math.round(durationMs / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;

  if (minutes === 0) {
    return `${seconds}秒`;
  }

  if (seconds === 0) {
    return `${minutes}分`;
  }

  return `${minutes}分${seconds}秒`;
}

function compareStickyTasksByUpdatedAt(left: Task, right: Task): number {
  return Date.parse(right.updatedAt) - Date.parse(left.updatedAt);
}
