import { describe, expect, it } from "vitest";
import {
  buildAssociativeMemoryEdges,
  deriveMemoryConcepts
} from "@core/memory/memoryAssociations";

describe("memory associations", () => {
  it("derives compact concepts from preference and event-like memory text", () => {
    expect([...deriveMemoryConcepts("用户喜欢吃火锅")]).toContain("火锅");
    expect([...deriveMemoryConcepts("用户提到一个时间相关事件：我明天要和阿杰去吃火锅。")]).toContain(
      "火锅"
    );
  });

  it("links a new event to an existing preference when they share a concept", () => {
    const edges = buildAssociativeMemoryEdges({
      newNode: {
        type: "event",
        content: "用户提到一个时间相关事件：我明天要和阿杰去吃火锅。",
        importance: 0.84
      },
      existingNodes: [
        {
          id: "memory_preference_hotpot",
          profileId: "profile_default",
          type: "preference",
          state: "active",
          content: "用户喜欢火锅",
          importance: 0.72,
          createdAt: "2026-05-04T12:00:00.000Z",
          updatedAt: "2026-05-04T12:00:00.000Z"
        }
      ]
    });

    expect(edges).toEqual([
      {
        targetNodeId: "memory_preference_hotpot",
        relationType: "preference_for",
        weight: 0.9
      }
    ]);
  });
});
