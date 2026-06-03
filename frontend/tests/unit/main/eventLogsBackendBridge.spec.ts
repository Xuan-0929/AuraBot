import { describe, expect, it, vi } from "vitest";
import { listEventLogsBackendFirst } from "../../../src/main/registerIpc";
import type { EventLogRecord } from "@shared/contracts/ipc";

describe("listEventLogsBackendFirst", () => {
  it("returns backend event logs when the backend read succeeds", async () => {
    const event: EventLogRecord = {
      id: "event-1",
      eventType: "memory.write",
      payload: { ok: true },
      createdAt: "2026-05-21T00:00:00.000Z"
    };
    const backendReader = vi.fn(() => Promise.resolve([event]));
    const localReader = vi.fn(() => []);

    const events = await listEventLogsBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      backendReader,
      localReader
    });

    expect(events).toEqual([event]);
    expect(backendReader).toHaveBeenCalledWith({
      storagePath: "/tmp/aurabot.sqlite"
    });
    expect(localReader).not.toHaveBeenCalled();
  });

  it("falls back to local event logs when the backend read fails", async () => {
    const fallbackEvent: EventLogRecord = {
      id: "event-local",
      eventType: "app.initial_state_loaded",
      payload: {},
      createdAt: "2026-05-21T00:00:00.000Z"
    };
    const backendReader = vi.fn(() => Promise.reject(new Error("offline")));
    const localReader = vi.fn(() => [fallbackEvent]);

    const events = await listEventLogsBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      backendReader,
      localReader
    });

    expect(events).toEqual([fallbackEvent]);
    expect(localReader).toHaveBeenCalledTimes(1);
  });
});
