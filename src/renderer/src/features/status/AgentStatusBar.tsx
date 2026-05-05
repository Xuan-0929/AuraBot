import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PetSettings } from "../../../../shared/schemas/petSettings";

const LABELS: Record<PetSettings["displayLanguage"], Record<AgentStatus, string>> = {
  en: {
    idle: "idle",
    listening: "listening",
    thinking: "thinking",
    typing: "typing",
    tasking: "tasking"
  },
  zh: {
    idle: "空闲中",
    listening: "倾听中",
    thinking: "判断中",
    typing: "对方正在输入中",
    tasking: "任务中"
  }
};

export function AgentStatusBar({
  status,
  displayLanguage = "en"
}: {
  status: AgentStatus;
  displayLanguage?: PetSettings["displayLanguage"];
}) {
  return (
    <div className="status-bar" aria-label="Agent status">
      <span className={`status-dot status-dot-${status}`} />
      <span>{LABELS[displayLanguage][status]}</span>
    </div>
  );
}
