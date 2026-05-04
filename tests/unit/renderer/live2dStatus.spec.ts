import { describe, expect, test } from "vitest";
import { getLive2DMotionForAgentStatus } from "../../../src/renderer/src/features/live2d/live2dStatus";

describe("getLive2DMotionForAgentStatus", () => {
  test.each([
    ["idle", "Idle"],
    ["listening", "Idle"],
    ["thinking", "TapBody"],
    ["replying", "TapBody"],
    ["tasking", "TapBody"]
  ] as const)("maps %s to the expected available Hiyori motion group", (status, group) => {
    expect(getLive2DMotionForAgentStatus(status).group).toBe(group);
  });
});
