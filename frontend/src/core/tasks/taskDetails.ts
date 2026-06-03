import { basename } from "node:path";
import type { TaskDetail, TaskDetailExecutor, TaskDetailTimelineItem } from "@shared/contracts/taskDetail";
import type { Task, TaskEvent } from "@shared/schemas/task";
import type { TaskArtifact } from "../storage/repositories/taskArtifactRepository";
import type { TaskInputArtifact } from "../storage/repositories/taskInputArtifactRepository";

const STDIO_KEYS = new Set(["stdout", "stderr"]);
const ERROR_EVENT_TYPES = new Set(["task.failed", "task.agent.cancelled"]);

export function buildTaskDetail(
  task: Task,
  events: TaskEvent[],
  artifacts: TaskArtifact[] = [],
  inputArtifacts: TaskInputArtifact[] = []
): TaskDetail {
  const timeline = events.map(toTimelineItem);
  const startedAt = firstStringFromTimeline(timeline, "startedAt");
  const completedAt = lastStringFromTimeline(timeline, "completedAt");
  const durationMs = lastNumberFromTimeline(timeline, "durationMs") ?? calculateDurationMs(startedAt, completedAt);

  return {
    task,
    executor: findExecutor(events),
    inputArtifacts: inputArtifacts.map((artifact) => ({
      ...artifact,
      name: artifact.originalName
    })),
    artifacts: artifacts.map((artifact) => ({
      ...artifact,
      name: basename(artifact.uri)
    })),
    timeline,
    recentError: findRecentError(task, events),
    startedAt,
    completedAt,
    durationMs
  };
}

function toTimelineItem(event: TaskEvent): TaskDetailTimelineItem {
  return {
    id: event.id,
    eventType: event.eventType,
    summary: event.summary,
    createdAt: event.createdAt,
    startedAt: readString(event.payload.startedAt),
    completedAt: readString(event.payload.completedAt),
    durationMs: readNumber(event.payload.durationMs),
    payloadSummary: summarizePayload(event.payload)
  };
}

function findExecutor(events: TaskEvent[]): TaskDetailExecutor | undefined {
  for (const event of events) {
    const executor: TaskDetailExecutor = {};
    const runnerId = readString(event.payload.runnerId);
    const cwd = readString(event.payload.cwd);
    const sandbox = readString(event.payload.sandbox);
    const command = readString(event.payload.command);
    const model = readString(event.payload.model);
    const approvalPolicy = readString(event.payload.approvalPolicy);

    if (runnerId) {
      executor.runnerId = runnerId;
    }
    if (cwd) {
      executor.cwd = cwd;
    }
    if (sandbox) {
      executor.sandbox = sandbox;
    }
    if (command) {
      executor.command = command;
    }
    if (model) {
      executor.model = model;
    }
    if (approvalPolicy) {
      executor.approvalPolicy = approvalPolicy;
    }

    if (Object.keys(executor).length > 0) {
      return executor;
    }
  }

  return undefined;
}

function summarizePayload(payload: Record<string, unknown>): Record<string, string | number | boolean> {
  const summary: Record<string, string | number | boolean> = {};

  for (const [key, value] of Object.entries(payload)) {
    if (STDIO_KEYS.has(key)) {
      if (typeof value === "string") {
        summary[`${key}Bytes`] = Buffer.byteLength(value, "utf8");
      }
      continue;
    }

    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      summary[key] = value;
    }
  }

  return summary;
}

function findRecentError(task: Task, events: TaskEvent[]): string | undefined {
  if (task.status !== "failed" && task.status !== "interrupted") {
    return undefined;
  }

  return events
    .slice()
    .reverse()
    .find((event) => ERROR_EVENT_TYPES.has(event.eventType) || event.eventType.includes("failed"))?.summary;
}

function firstStringFromTimeline(items: TaskDetailTimelineItem[], key: "startedAt" | "completedAt"): string | undefined {
  return items.find((item) => item[key])?.[key];
}

function lastStringFromTimeline(items: TaskDetailTimelineItem[], key: "startedAt" | "completedAt"): string | undefined {
  return items
    .slice()
    .reverse()
    .find((item) => item[key])?.[key];
}

function lastNumberFromTimeline(items: TaskDetailTimelineItem[], key: "durationMs"): number | undefined {
  return items
    .slice()
    .reverse()
    .find((item) => typeof item[key] === "number")?.[key];
}

function calculateDurationMs(startedAt?: string, completedAt?: string): number | undefined {
  if (!startedAt || !completedAt) {
    return undefined;
  }

  const started = Date.parse(startedAt);
  const completed = Date.parse(completedAt);

  if (!Number.isFinite(started) || !Number.isFinite(completed) || completed < started) {
    return undefined;
  }

  return completed - started;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
