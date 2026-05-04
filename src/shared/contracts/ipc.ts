import type { AppInitialState, SendChatMessageInput, SendChatMessageResult } from "./chat";
import type { PetActivity } from "./pet";
import type { PetSettings, PetSettingsPatch } from "../schemas/petSettings";

export const IPC_CHANNELS = {
  getInitialState: "app:get-initial-state",
  openConsoleWindow: "window:open-console",
  getPetSettings: "pet:get-settings",
  updatePetSettings: "pet:update-settings",
  setPetMousePassthrough: "pet:set-mouse-passthrough",
  movePetWindowBy: "pet:move-window-by",
  petSettingsChanged: "pet:settings-changed",
  petActivity: "pet:activity",
  agentStatusChanged: "agent:status-changed",
  sendChatMessage: "chat:send-message",
  getEventLogs: "debug:get-event-logs"
} as const;

export interface EventLogRecord {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface AuraBotApi {
  getInitialState(): Promise<AppInitialState>;
  openConsoleWindow(): Promise<void>;
  getPetSettings(): Promise<PetSettings>;
  updatePetSettings(patch: PetSettingsPatch): Promise<PetSettings>;
  setPetMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  onPetSettingsChanged(listener: (settings: PetSettings) => void): () => void;
  onPetActivity(listener: (activity: PetActivity) => void): () => void;
  onAgentStatusChanged(listener: (status: import("./chat").AgentStatus) => void): () => void;
  sendChatMessage(input: SendChatMessageInput): Promise<SendChatMessageResult>;
  getEventLogs(): Promise<EventLogRecord[]>;
}
