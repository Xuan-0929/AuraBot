/**
 * 验证 Electron 使用 Python Backend 的 health 与 Memory 产品契约。
 *
 * 测试替换 fetch 边界，确认浏览、忘记、失败任务重试的路径、camelCase body 与
 * 稳定错误均不暴露内部 payload、lease 或 SQLite 细节。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BackendGatewayError,
  forgetBackendMemories,
  getBackendMemorySnapshot,
  getBackendStatus,
  retryBackendMemoryJob
} from "../../../src/main/backendGatewayClient";

afterEach(() => vi.unstubAllGlobals());

describe("getBackendStatus", () => {
  it("reports online from the exact health response using one request", async () => {
    const fetchMock = vi.fn(async (_url: string) => ({
      ok: true,
      json: async () => ({ ok: true, service: "backend" })
    }));
    vi.stubGlobal("fetch", fetchMock);

    const status = await getBackendStatus({ baseUrl: "http://backend.test" });

    expect(status).toMatchObject({
      online: true,
      gateway: { ok: true, service: "backend" }
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://backend.test/health");
  });
});

describe("Python Memory gateway client", () => {
  it("browses bounded active memories and public jobs", async () => {
    const snapshot = {
      items: [{
        memoryId: "memory-1",
        episodeId: "episode-1",
        type: "preference",
        content: "用户喜欢乌龙茶",
        createdAt: "2026-08-28T00:00:00.000Z",
        updatedAt: "2026-08-28T00:00:00.000Z"
      }],
      jobs: [{
        jobId: "job-1",
        kind: "extraction",
        state: "failed",
        attemptCount: 4,
        lastError: "provider unavailable",
        updatedAt: "2026-08-28T00:00:01.000Z"
      }]
    };
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(snapshot), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(getBackendMemorySnapshot({
      baseUrl: "http://backend.test",
      limit: 25
    })).resolves.toEqual(snapshot);
    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend.test/api/v1/memories?limit=25",
      expect.objectContaining({ method: "GET" })
    );
  });

  it("forgets selected memories and retries one failed public job", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        forgottenCount: 1,
        memoryIds: ["memory-1"]
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        jobId: "job/1",
        kind: "extraction",
        state: "pending",
        attemptCount: 0,
        lastError: null,
        updatedAt: "2026-08-28T00:00:02.000Z"
      }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(forgetBackendMemories({
      memoryIds: ["memory-1"],
      episodeIds: [],
      reason: "user request"
    }, { baseUrl: "http://backend.test" })).resolves.toEqual({
      forgottenCount: 1,
      memoryIds: ["memory-1"]
    });
    await expect(retryBackendMemoryJob("job/1", {
      baseUrl: "http://backend.test"
    })).resolves.toMatchObject({ state: "pending", attemptCount: 0 });

    expect(fetchMock.mock.calls[0]).toEqual([
      "http://backend.test/api/v1/memories/forget",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          memoryIds: ["memory-1"],
          episodeIds: [],
          reason: "user request"
        })
      })
    ]);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://backend.test/api/v1/memory-jobs/job%2F1/retry"
    );
  });

  it("throws a stable public error for rejected Memory operations", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: { code: "memory_job_not_found", message: "Memory job was not found." }
    }), { status: 404 })));

    const operation = retryBackendMemoryJob("missing", { baseUrl: "http://backend.test" });

    await expect(operation).rejects.toBeInstanceOf(BackendGatewayError);
    await expect(operation).rejects.toMatchObject({
      status: 404,
      code: "memory_job_not_found",
      message: "Memory job was not found."
    });
  });
});
