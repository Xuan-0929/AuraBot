import type { AgentStatus } from "@shared/contracts/chat";

export interface Live2DMotionDescriptor {
  group: "Idle" | "TapBody";
  index?: number;
}

/**
 * Ambient motion driven by agent status: always Idle so the pet stays in a
 * continuous idle loop. Non-idle gestures (e.g. TapBody) should be triggered
 * only via explicit `requestMotion` calls, not from agent status alone.
 */
const AMBIENT_IDLE: Live2DMotionDescriptor = { group: "Idle", index: 0 };

export function getLive2DMotionForAgentStatus(_status: AgentStatus): Live2DMotionDescriptor {
  return AMBIENT_IDLE;
}
