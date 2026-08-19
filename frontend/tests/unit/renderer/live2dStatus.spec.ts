/**
 * Live2D 状态映射测试：验证保留的聊天 Agent 状态选择正确表现。
 * 测试不包含旧执行态，确保桌宠动画只响应当前公共 AgentStatus。
 */
import { describe, expect, test } from "vitest";
import { getLive2DMotionForAgentStatus } from "../../../src/features/live2d/live2dStatus";

describe("getLive2DMotionForAgentStatus", () => {
  test.each([["idle"], ["listening"], ["thinking"], ["typing"]] as const)(
    "maps %s to ambient Idle (agent status does not drive TapBody)",
    (status) => {
      const motion = getLive2DMotionForAgentStatus(status);
      expect(motion.group).toBe("Idle");
      expect(motion.index).toBe(0);
    }
  );
});
