// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { MemoryDebugPanel } from "../../../src/renderer/src/features/memory/MemoryDebugPanel";
import type { MemoryDebugSnapshot } from "../../../src/shared/contracts/ipc";

describe("MemoryDebugPanel", () => {
  it("renders the latest recall trace with seed and graph expansion details", async () => {
    const container = document.createElement("div");
    const root = createRoot(container);
    const snapshot: MemoryDebugSnapshot = {
      generatedAt: "2026-05-05T12:00:00.000Z",
      nodes: [
        {
          id: "memory_event_ajie_hotpot",
          profileId: "profile-1",
          type: "event",
          state: "active",
          content: "用户明天要和阿杰去吃火锅",
          importance: 0.84,
          createdAt: "2026-05-05T11:00:00.000Z",
          updatedAt: "2026-05-05T11:00:00.000Z"
        },
        {
          id: "memory_preference_hotpot",
          profileId: "profile-1",
          type: "preference",
          state: "active",
          content: "用户喜欢火锅",
          importance: 0.72,
          createdAt: "2026-05-05T10:00:00.000Z",
          updatedAt: "2026-05-05T10:00:00.000Z"
        }
      ],
      edges: [],
      summaries: [],
      recentEvents: [],
      lastRecallTrace: {
        query: "阿杰那件事我该注意什么？",
        recallPlan: {
          queryTerms: ["阿杰"],
          targetTypes: ["event"],
          rankedNodeIds: ["memory_event_ajie_hotpot"]
        },
        seedNodeIds: ["memory_event_ajie_hotpot"],
        expandedNodeIds: ["memory_preference_hotpot"],
        returnedNodeIds: ["memory_event_ajie_hotpot", "memory_preference_hotpot"],
        traversedEdges: [
          {
            sourceNodeId: "memory_event_ajie_hotpot",
            targetNodeId: "memory_preference_hotpot",
            relationType: "preference_for",
            weight: 0.91,
            depth: 1
          }
        ]
      }
    };

    await act(async () => {
      root.render(<MemoryDebugPanel snapshot={snapshot} expanded onRefresh={vi.fn()} onToggle={vi.fn()} />);
    });

    expect(container.textContent).toContain("Last Recall");
    expect(container.textContent).toContain("阿杰那件事我该注意什么？");
    expect(container.textContent).toContain("Seeds 1");
    expect(container.textContent).toContain("Expanded 1");
    expect(container.textContent).toContain("阿杰");
    expect(container.textContent).toContain("preference_for");
    expect(container.textContent).toContain("event_ajie");
    expect(container.textContent).toContain("preference");

    await act(async () => {
      root.unmount();
    });
  });
});
