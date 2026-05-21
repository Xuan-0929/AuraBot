import { describe, expect, it } from "vitest";
import { collectRelatedMemoryNodeIds } from "../../../../packages/core-legacy/src/memory/graphTraversal";

describe("collectRelatedMemoryNodeIds", () => {
  it("returns bounded related node ids without revisiting cycles", () => {
    const result = collectRelatedMemoryNodeIds({
      startNodeIds: ["concert"],
      maxDepth: 2,
      maxNodes: 3,
      edges: [
        { sourceNodeId: "concert", targetNodeId: "music", weight: 0.8 },
        { sourceNodeId: "music", targetNodeId: "friend", weight: 0.7 },
        { sourceNodeId: "friend", targetNodeId: "concert", weight: 0.6 },
        { sourceNodeId: "concert", targetNodeId: "exam", weight: 0.2 }
      ]
    });

    expect(result).toEqual(["music", "friend", "exam"]);
  });
});
