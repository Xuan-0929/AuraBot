import type { TaskStatus } from "@shared/schemas/task";

const ACTIVE_STATUSES = new Set<TaskStatus>(["queued", "running", "waiting"]);
const SETTLED_STATUSES = new Set<TaskStatus>(["completed", "failed", "interrupted"]);
const TRANSITIONS = new Map<TaskStatus, Set<TaskStatus>>([
  ["queued", new Set(["running", "interrupted"])],
  ["running", new Set(["waiting", "completed", "failed", "interrupted"])],
  ["waiting", new Set(["running", "completed", "failed", "interrupted"])],
  ["failed", new Set(["queued"])],
  ["interrupted", new Set(["queued"])],
  ["completed", new Set()]
]);

export function isActiveTask(status: TaskStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

export function isSettledTask(status: TaskStatus): boolean {
  return SETTLED_STATUSES.has(status);
}

export function isRunnableTask(status: TaskStatus): boolean {
  return status === "queued";
}

export function isCancelableTask(status: TaskStatus): boolean {
  return isActiveTask(status);
}

export function isDeletableTask(status: TaskStatus): boolean {
  return isSettledTask(status);
}

export function canTransitionTaskStatus(from: TaskStatus, to: TaskStatus): boolean {
  return TRANSITIONS.get(from)?.has(to) ?? false;
}
