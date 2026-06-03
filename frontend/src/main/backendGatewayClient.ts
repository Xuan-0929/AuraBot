import type {
  BackendGatewayHealth,
  BackendServiceEndpoint,
  BackendStatus
} from "@shared/contracts/backend";
import type { MainSessionMessagesSnapshot } from "@shared/contracts/chat";
import type { EventLogRecord, MemoryDebugSnapshot } from "@shared/contracts/ipc";
import type { TaskDebugSnapshot } from "@shared/contracts/taskDetail";
import type { Task } from "@shared/schemas/task";

const DEFAULT_BACKEND_GATEWAY_URL = "http://127.0.0.1:6401";
const DEFAULT_BACKEND_TIMEOUT_MS = 750;

export interface BackendStatusClientOptions {
  baseUrl?: string;
  timeoutMs?: number;
}

interface ServicesPayload {
  services?: Array<{
    name?: unknown;
    host?: unknown;
    port?: unknown;
  }>;
}

interface EventLogsPayload {
  events?: EventLogRecord[];
}

interface TasksPayload {
  tasks?: Task[];
}

export async function getBackendStatus(options: BackendStatusClientOptions = {}): Promise<BackendStatus> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const checkedAt = new Date().toISOString();

  try {
    const [gateway, servicesPayload] = await Promise.all([
      fetchJson<BackendGatewayHealth>(`${baseUrl}/health`, timeoutMs),
      fetchJson<ServicesPayload>(`${baseUrl}/services`, timeoutMs)
    ]);

    return {
      online: Boolean(gateway.ok),
      checkedAt,
      gateway,
      services: normalizeServices(servicesPayload)
    };
  } catch (error) {
    return {
      online: false,
      checkedAt,
      services: [],
      error: `Backend gateway is unavailable: ${errorToMessage(error)}`
    };
  }
}

export async function getBackendEventLogs(
  options: BackendStatusClientOptions & {
    storagePath: string;
    limit?: number;
  }
): Promise<EventLogRecord[]> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const params = new URLSearchParams({
    storage_path: options.storagePath,
    limit: String(options.limit ?? 100)
  });

  try {
    const payload = await fetchJson<EventLogsPayload>(`${baseUrl}/debug/events?${params}`, timeoutMs);

    return payload.events ?? [];
  } catch (error) {
    throw new Error(`Backend event logs are unavailable: ${errorToMessage(error)}`);
  }
}

export async function getBackendMainSessionMessages(
  options: BackendStatusClientOptions & {
    storagePath: string;
    profileId: string;
  }
): Promise<MainSessionMessagesSnapshot> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const params = new URLSearchParams({
    storage_path: options.storagePath,
    profile_id: options.profileId
  });

  try {
    return await fetchJson<MainSessionMessagesSnapshot>(`${baseUrl}/chat/main-session?${params}`, timeoutMs);
  } catch (error) {
    throw new Error(`Backend main session messages are unavailable: ${errorToMessage(error)}`);
  }
}

export async function getBackendTasks(
  options: BackendStatusClientOptions & {
    storagePath: string;
    profileId: string;
  }
): Promise<Task[]> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const params = new URLSearchParams({
    storage_path: options.storagePath,
    profile_id: options.profileId
  });

  try {
    const payload = await fetchJson<TasksPayload>(`${baseUrl}/tasks?${params}`, timeoutMs);

    return payload.tasks ?? [];
  } catch (error) {
    throw new Error(`Backend tasks are unavailable: ${errorToMessage(error)}`);
  }
}

export async function getBackendMemoryDebugSnapshot(
  options: BackendStatusClientOptions & {
    storagePath: string;
    profileId: string;
    eventLimit?: number;
  }
): Promise<MemoryDebugSnapshot> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const params = new URLSearchParams({
    storage_path: options.storagePath,
    profile_id: options.profileId,
    event_limit: String(options.eventLimit ?? 80)
  });

  try {
    return await fetchJson<MemoryDebugSnapshot>(`${baseUrl}/debug/memory?${params}`, timeoutMs);
  } catch (error) {
    throw new Error(`Backend memory debug snapshot is unavailable: ${errorToMessage(error)}`);
  }
}

export async function getBackendTaskDebugSnapshot(
  options: BackendStatusClientOptions & {
    storagePath: string;
    profileId: string;
    limit?: number;
  }
): Promise<TaskDebugSnapshot> {
  const baseUrl = options.baseUrl ?? DEFAULT_BACKEND_GATEWAY_URL;
  const timeoutMs = options.timeoutMs ?? DEFAULT_BACKEND_TIMEOUT_MS;
  const params = new URLSearchParams({
    storage_path: options.storagePath,
    profile_id: options.profileId,
    limit: String(options.limit ?? 50)
  });

  try {
    return await fetchJson<TaskDebugSnapshot>(`${baseUrl}/debug/tasks?${params}`, timeoutMs);
  } catch (error) {
    throw new Error(`Backend task debug snapshot is unavailable: ${errorToMessage(error)}`);
  }
}

async function fetchJson<T>(url: string, timeoutMs: number): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      signal: controller.signal
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    return await response.json() as T;
  } finally {
    clearTimeout(timeout);
  }
}

function normalizeServices(payload: ServicesPayload): BackendServiceEndpoint[] {
  return (payload.services ?? [])
    .filter((service): service is { name: string; host: string; port: number } =>
      typeof service.name === "string" &&
      typeof service.host === "string" &&
      typeof service.port === "number"
    )
    .map((service) => ({
      ...service,
      url: `http://${service.host}:${service.port}`
    }));
}

function errorToMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.name === "AbortError" ? "request timed out" : error.message;
  }

  return String(error);
}
