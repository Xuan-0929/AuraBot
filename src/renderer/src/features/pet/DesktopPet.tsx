import type { AgentStatus } from "../../../../shared/contracts/chat";
import { AgentStatusBar } from "../status/AgentStatusBar";
import { Live2DModelCanvas } from "../live2d/Live2DModelCanvas";

export interface DesktopPetProps {
  agentStatus: AgentStatus;
  onOpenConsole(): Promise<void>;
}

const MODEL_PATH = "assets/live2d/hiyori/Hiyori.model3.json";

export function DesktopPet({ agentStatus, onOpenConsole }: DesktopPetProps) {
  return (
    <main className="desktop-pet" aria-label="Desktop Live2D pet">
      <button
        className="desktop-pet-model-button"
        type="button"
        aria-label="Open AuraBot console"
        onClick={() => void onOpenConsole()}
      >
        <span className="desktop-pet-model-shell" data-testid="live2d-model-surface">
          <Live2DModelCanvas modelPath={MODEL_PATH} compact />
        </span>
      </button>
      <div className="desktop-pet-status">
        <AgentStatusBar status={agentStatus} />
      </div>
    </main>
  );
}
