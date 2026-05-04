import type { AgentStatus } from "../../../../shared/contracts/chat";

const LABELS: Record<AgentStatus, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  replying: "Replying",
  tasking: "Tasking"
};

export function AgentStatusBar({ status }: { status: AgentStatus }) {
  return (
    <div className="status-bar" aria-label="Agent status">
      <span className={`status-dot status-dot-${status}`} />
      <span>{LABELS[status]}</span>
    </div>
  );
}
