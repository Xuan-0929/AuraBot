import { describe, expect, it, vi } from "vitest";
import { loadTaskDebugSnapshotBackendFirst } from "../../../src/main/registerIpc";
import type { TaskDebugSnapshot } from "@shared/contracts/taskDetail";

describe("loadTaskDebugSnapshotBackendFirst", () => {
  it("returns backend task debug data when the backend read succeeds", async () => {
    const backendSnapshot: TaskDebugSnapshot = {
      generatedAt: "2026-05-21T00:00:00.000Z",
      tasks: []
    };
    const backendReader = vi.fn(() => Promise.resolve(backendSnapshot));
    const localReader = vi.fn(() => ({
      generatedAt: "local",
      tasks: []
    }));

    const snapshot = await loadTaskDebugSnapshotBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader
    });

    expect(snapshot).toEqual(backendSnapshot);
    expect(backendReader).toHaveBeenCalledWith({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1"
    });
    expect(localReader).not.toHaveBeenCalled();
  });

  it("falls back to local task debug data when the backend read fails", async () => {
    const localSnapshot: TaskDebugSnapshot = {
      generatedAt: "local",
      tasks: []
    };
    const backendReader = vi.fn(() => Promise.reject(new Error("offline")));
    const localReader = vi.fn(() => localSnapshot);

    const snapshot = await loadTaskDebugSnapshotBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader
    });

    expect(snapshot).toEqual(localSnapshot);
    expect(localReader).toHaveBeenCalledTimes(1);
  });
});
