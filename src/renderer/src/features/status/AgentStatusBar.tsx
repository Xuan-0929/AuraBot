import type { AgentStatus } from "../../../../shared/contracts/chat";
import type { PetSettings } from "../../../../shared/schemas/petSettings";
import { agentStatusLabel } from "../../i18n";

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
      <span>{agentStatusLabel(displayLanguage, status)}</span>
    </div>
  );
}
