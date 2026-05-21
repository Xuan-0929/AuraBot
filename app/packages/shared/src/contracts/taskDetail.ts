import type { Task } from "../schemas/task";

export interface TaskDetailArtifact {
  id: string;
  taskId: string;
  artifactType: string;
  uri: string;
  name: string;
  createdAt: string;
}

export interface TaskDetailInputArtifact {
  id: string;
  taskId: string;
  artifactType: string;
  originalName: string;
  mimeType: string;
  sizeBytes: number;
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
  inputArtifacts: TaskDetailInputArtifact[];
  artifacts: TaskDetailArtifact[];
  timeline: TaskDetailTimelineItem[];
  recentError?: string;
  startedAt?: string;
  completedAt?: string;
  durationMs?: number;
}

export interface TaskDebugSnapshot {
  generatedAt: string;
  tasks: TaskDetail[];
}
