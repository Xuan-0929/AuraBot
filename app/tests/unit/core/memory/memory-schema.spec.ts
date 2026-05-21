import { describe, expect, it } from "vitest";
import { memoryNodeSchema, memoryWriteRequestSchema } from "../../../../packages/shared/src/schemas/memory";

describe("memory schemas", () => {
  it("accepts event and preference memory while rejecting unknown node types", () => {
    expect(
      memoryNodeSchema.parse({
        id: "mem_1",
        profileId: "profile_default",
        type: "event",
        state: "active",
        content: "User plans to attend a concert next week.",
        importance: 0.8,
        createdAt: "2026-05-04T12:00:00.000Z",
        updatedAt: "2026-05-04T12:00:00.000Z"
      })
    ).toMatchObject({ type: "event" });

    expect(() =>
      memoryWriteRequestSchema.parse({
        profileId: "profile_default",
        sourceSessionId: "session_main",
        node: {
          type: "random",
          content: "Invalid memory node.",
          importance: 0.3
        }
      })
    ).toThrow();
  });
});
