import { describe, expect, it, vi } from "vitest";
import { loadTasksBackendFirst } from "../../../src/main/registerIpc";
import type { Task } from "@shared/schemas/task";

describe("loadTasksBackendFirst", () => {
  it("returns backend tasks when the backend read succeeds", async () => {
    const backendTask = task("backend-task", "后端任务");
    const backendReader = vi.fn(() => Promise.resolve([backendTask]));
    const localReader = vi.fn(() => []);

    const tasks = await loadTasksBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader
    });

    expect(tasks).toEqual([backendTask]);
    expect(backendReader).toHaveBeenCalledWith({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1"
    });
    expect(localReader).not.toHaveBeenCalled();
  });

  it("falls back to local tasks when the backend read fails", async () => {
    const fallbackTask = task("local-task", "本地任务");
    const backendReader = vi.fn(() => Promise.reject(new Error("offline")));
    const localReader = vi.fn(() => [fallbackTask]);

    const tasks = await loadTasksBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader
    });

    expect(tasks).toEqual([fallbackTask]);
    expect(localReader).toHaveBeenCalledWith("profile-1");
  });
});

function task(id: string, title: string): Task {
  return {
    id,
    profileId: "profile-1",
    parentSessionId: "session-main",
    sessionId: "session-task",
    kind: "result_output",
    title,
    status: "queued",
    latestProgress: "Task queued from main chat.",
    createdAt: "2026-05-21T00:00:00.000Z",
    updatedAt: "2026-05-21T00:00:01.000Z"
  };
}
