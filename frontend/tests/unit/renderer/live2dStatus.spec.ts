import { describe, expect, test } from "vitest";
import { getLive2DMotionForAgentStatus } from "../../../src/features/live2d/live2dStatus";

describe("getLive2DMotionForAgentStatus", () => {
  test.each([["idle"], ["listening"], ["thinking"], ["typing"], ["tasking"]] as const)(
    "maps %s to ambient Idle (agent status does not drive TapBody)",
    (status) => {
      const motion = getLive2DMotionForAgentStatus(status);
      expect(motion.group).toBe("Idle");
      expect(motion.index).toBe(0);
    }
  );
});
