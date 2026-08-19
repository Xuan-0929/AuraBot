/**
 * 探测 health-only Python Backend 并返回桌面可展示状态。
 *
 * 客户端只请求 GET /health，不再读取 services、Task、chat 或 debug endpoints；
 * Electron 当前业务数据继续由本地 repositories 提供。
 */

import type { BackendGatewayHealth, BackendStatus } from "@shared/contracts/backend";

const DEFAULT_BACKEND_GATEWAY_URL = "http://127.0.0.1:6401";
const DEFAULT_BACKEND_TIMEOUT_MS = 750;

export interface BackendStatusClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
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

async function fetchJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timeout);
  }
}

function errorToMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.name === "AbortError" ? "request timed out" : error.message;
  }
  return String(error);
}
