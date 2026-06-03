import { describe, expect, it, vi } from "vitest";
import { loadMemoryDebugSnapshotBackendFirst } from "../../../src/main/registerIpc";
import type { MemoryDebugSnapshot } from "@shared/contracts/ipc";

describe("loadMemoryDebugSnapshotBackendFirst", () => {
  it("uses backend memory data and preserves the local recall trace", async () => {
    const backendSnapshot: MemoryDebugSnapshot = {
      generatedAt: "2026-05-21T00:00:00.000Z",
      nodes: [
        {
          id: "node-1",
          profileId: "profile-1",
          type: "preference",
          state: "active",
          content: "喜欢火锅",
          importance: 0.8,
          createdAt: "2026-05-21T00:00:00.000Z",
          updatedAt: "2026-05-21T00:00:01.000Z"
        }
      ],
      edges: [],
      summaries: [],
      recentEvents: []
    };
    const localTrace = {
      query: "火锅",
      recallPlan: {
        queryTerms: ["火锅"],
        targetTypes: ["preference" as const],
        rankedNodeIds: ["node-1"]
      },
      seedNodeIds: ["node-1"],
      expandedNodeIds: ["node-1"],
      returnedNodeIds: ["node-1"],
      traversedEdges: []
    };
    const backendReader = vi.fn(() => Promise.resolve(backendSnapshot));
    const localReader = vi.fn(() => ({
      generatedAt: "local",
      nodes: [],
      edges: [],
      summaries: [],
      recentEvents: [],
      lastRecallTrace: localTrace
    }));

    const snapshot = await loadMemoryDebugSnapshotBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader,
      getLastRecallTrace: () => localTrace
    });

    expect(snapshot.nodes).toEqual(backendSnapshot.nodes);
    expect(snapshot.lastRecallTrace).toEqual(localTrace);
    expect(localReader).not.toHaveBeenCalled();
  });

  it("falls back to the local memory snapshot when the backend read fails", async () => {
    const fallbackSnapshot: MemoryDebugSnapshot = {
      generatedAt: "local",
      nodes: [],
      edges: [],
      summaries: [],
      recentEvents: []
    };
    const backendReader = vi.fn(() => Promise.reject(new Error("offline")));
    const localReader = vi.fn(() => fallbackSnapshot);

    const snapshot = await loadMemoryDebugSnapshotBackendFirst({
      storagePath: "/tmp/aurabot.sqlite",
      profileId: "profile-1",
      backendReader,
      localReader,
      getLastRecallTrace: () => undefined
    });

    expect(snapshot).toEqual(fallbackSnapshot);
    expect(localReader).toHaveBeenCalledTimes(1);
  });
});
