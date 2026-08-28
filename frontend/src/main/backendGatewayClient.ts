/**
 * 访问 Python Backend 的 health 与 Memory 管理产品接口。
 *
 * 客户端执行有界超时、camelCase JSON 与稳定公开错误转换；它不读取旧 services、
 * Task/debug 路径，也不会把失败请求回落到 TypeScript Memory。
 */

import type {
  BackendGatewayHealth,
  BackendMemoryJob,
  BackendMemorySnapshot,
  BackendStatus,
  ForgetBackendMemoriesInput,
  ForgetBackendMemoriesResult
} from "@shared/contracts/backend";

export const DEFAULT_BACKEND_GATEWAY_URL = "http://127.0.0.1:6401";
const DEFAULT_BACKEND_TIMEOUT_MS = 750;

export interface BackendStatusClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
}

export interface BackendMemoryClientOptions extends BackendStatusClientOptions {
  limit?: number;
}

export class BackendGatewayError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string
  ) {
    super(message);
    this.name = "BackendGatewayError";
  }
}

export async function getBackendStatus(
  options: BackendStatusClientOptions = {}
): Promise<BackendStatus> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const checkedAt = new Date().toISOString();
  try {
    const gateway = await fetchJson<BackendGatewayHealth>(`${baseUrl}/health`, timeoutMs);
    return {
      online: gateway.ok === true && gateway.service === "backend",
      checkedAt,
      gateway
    };
  } catch (error) {
    return {
      online: false,
      checkedAt,
      error: `Backend gateway is unavailable: ${errorToMessage(error)}`
    };
  }
}

export async function getBackendMemorySnapshot(
  options: BackendMemoryClientOptions = {}
): Promise<BackendMemorySnapshot> {
  const limit = options.limit ?? 50;
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) {
    throw new BackendGatewayError(400, "invalid_memory_request", "limit must be from 1 to 100");
  }
  return fetchJson<BackendMemorySnapshot>(
    `${normalizedBaseUrl(options.baseUrl)}/api/v1/memories?limit=${limit}`,
    options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS,
    { method: "GET" }
  );
}

export async function forgetBackendMemories(
  input: ForgetBackendMemoriesInput,
  options: BackendStatusClientOptions = {}
): Promise<ForgetBackendMemoriesResult> {
  return fetchJson<ForgetBackendMemoriesResult>(
    `${normalizedBaseUrl(options.baseUrl)}/api/v1/memories/forget`,
    options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    }
  );
}

export async function retryBackendMemoryJob(
  jobId: string,
  options: BackendStatusClientOptions = {}
): Promise<BackendMemoryJob> {
  return fetchJson<BackendMemoryJob>(
    `${normalizedBaseUrl(options.baseUrl)}/api/v1/memory-jobs/${encodeURIComponent(jobId)}/retry`,
    options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS,
    { method: "POST" }
  );
}

async function fetchJson<T>(
  url: string,
  timeoutMs: number,
  init: RequestInit = {}
): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    if (!response.ok) {
      throw await publicGatewayError(response);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

async function publicGatewayError(response: Response): Promise<BackendGatewayError> {
  try {
    const body = await response.json() as {
      error?: { code?: unknown; message?: unknown };
    };
    const code = typeof body.error?.code === "string"
      ? body.error.code
      : "backend_request_failed";
    const message = typeof body.error?.message === "string"
      ? body.error.message
      : `Backend request failed with HTTP ${response.status}`;
    return new BackendGatewayError(response.status, code, message);
  } catch {
    return new BackendGatewayError(
      response.status,
      "backend_request_failed",
      `Backend request failed with HTTP ${response.status}`
    );
  }
}

function normalizedBaseUrl(baseUrl: string | undefined): string {
  return (baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL).replace(/\/$/, "");
}

function errorToMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.name === "AbortError" ? "request timed out" : error.message;
  }
  return String(error);
}
