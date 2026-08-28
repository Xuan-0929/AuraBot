/**
 * 定义 Electron 对 Python Backend health 与 Memory 管理面的公共投影。
 *
 * DTO 只包含 active Memory 与公开 job 摘要，不包含提取 payload、lease、向量、SQLite
 * 或 Qdrant 内部字段；Task 与 Main Agent 实时协议仍由各自边界管理。
 */

export interface BackendGatewayHealth {
  ok: boolean;
  service: string;
}

export interface BackendStatus {
  online: boolean;
  checkedAt: string;
  gateway?: BackendGatewayHealth;
  error?: string;
}

export interface BackendMemoryItem {
  memoryId: string;
  episodeId: string;
  type: string;
  content: string;
  createdAt: string;
  updatedAt: string;
}

export interface BackendMemoryJob {
  jobId: string;
  kind: "extraction" | "vectorDelete" | "vectorUpsert";
  state: string;
  attemptCount: number;
  lastError: string | null;
  updatedAt: string | null;
}

export interface BackendMemorySnapshot {
  items: BackendMemoryItem[];
  jobs: BackendMemoryJob[];
}

export interface ForgetBackendMemoriesInput {
  memoryIds: string[];
  episodeIds: string[];
  reason?: string;
}

export interface ForgetBackendMemoriesResult {
  forgottenCount: number;
  memoryIds: string[];
}
