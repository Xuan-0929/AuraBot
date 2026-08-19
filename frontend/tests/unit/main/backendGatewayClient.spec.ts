/**
 * 验证 Electron 只使用 Python Backend 的 health-only contract。
 *
 * 测试替换 fetch 边界，确认不再请求 /services 或任何 Task/debug 路径。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { getBackendStatus } from "../../../src/main/backendGatewayClient";

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
