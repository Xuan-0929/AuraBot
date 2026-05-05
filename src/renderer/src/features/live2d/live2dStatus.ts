import type { AgentStatus } from "../../../../shared/contracts/chat";

export interface Live2DMotionDescriptor {
  group: "Idle" | "TapBody";
  index?: number;
}

const STATUS_MOTIONS: Record<AgentStatus, Live2DMotionDescriptor> = {
  idle: { group: "Idle", index: 0 },
  listening: { group: "Idle", index: 1 },
  thinking: { group: "TapBody" },
  typing: { group: "TapBody" },
  tasking: { group: "TapBody" }
};

export function getLive2DMotionForAgentStatus(status: AgentStatus): Live2DMotionDescriptor {
  return STATUS_MOTIONS[status];
}
