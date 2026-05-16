import type { Task } from "../schemas/task";

export interface TaskDetailArtifact {
  id: string;
  taskId: string;
  artifactType: string;
  uri: string;
  name: string;
  createdAt: string;
}

export interface TaskDetailExecutor {
  runnerId?: string;
  cwd?: string;
  sandbox?: string;
  command?: string;
  model?: string;
  approvalPolicy?: string;
}

export interface TaskDetailTimelineItem {
  id: string;
  eventType: string;
  summary: string;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
  payloadSummary: Record<string, string | number | boolean>;
}

export interface TaskDetail {
  task: Task;
  executor?: TaskDetailExecutor;
  artifacts: TaskDetailArtifact[];
  timeline: TaskDetailTimelineItem[];
  recentError?: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
}
