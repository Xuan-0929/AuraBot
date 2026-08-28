/**
 * Verifies that packaged-origin normalization is restricted to the Pet webContents and the local Main
 * Agent gateway. This pure test guards the security predicate without starting Electron or broadening
 * Console, Settings, external pages, or arbitrary localhost requests.
 */

import { describe, expect, it } from "vitest";
import { normalizedMainAgentHeaders } from "@renderer/main/mainAgentOriginBridge";

describe("Main Agent packaged Pet origin bridge", () => {
  it("normalizes only Pet requests to the exact local HTTP and WebSocket gateway", () => {
    const headers = { Origin: "null", Accept: "application/json" };

    expect(normalizedMainAgentHeaders({
      url: "http://127.0.0.1:6401/api/v1/main-agent/realtime-token",
      webContentsId: 42,
      requestHeaders: headers,
    }, 42)).toEqual({ ...headers, Origin: "http://127.0.0.1:6400" });
    expect(normalizedMainAgentHeaders({
      url: "ws://127.0.0.1:6401/api/v1/main-agent/realtime",
      webContentsId: 42,
      requestHeaders: headers,
    }, 42)?.Origin).toBe("http://127.0.0.1:6400");

    expect(normalizedMainAgentHeaders({
      url: "http://127.0.0.1:6401/api/v1/main-agent/realtime-token",
      webContentsId: 7,
      requestHeaders: headers,
    }, 42)).toBeNull();
    expect(normalizedMainAgentHeaders({
      url: "http://localhost:6401/api/v1/main-agent/realtime-token",
      webContentsId: 42,
      requestHeaders: headers,
    }, 42)).toBeNull();
    expect(normalizedMainAgentHeaders({
      url: "https://example.com/",
      webContentsId: 42,
      requestHeaders: headers,
    }, 42)).toBeNull();
  });
});
