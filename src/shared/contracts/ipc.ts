import type { AppInitialState, SendChatMessageInput, SendChatMessageResult } from "./chat";
import type { PetActivity } from "./pet";
import type { Message } from "../schemas/message";
import type { MemoryEdge, MemoryNode, MemorySummary } from "../schemas/memory";
import type { PetSettings, PetSettingsPatch } from "../schemas/petSettings";
import type { Task } from "../schemas/task";

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
  chatMessageCreated: "chat:message-created",
  sendChatMessage: "chat:send-message",
  taskChanged: "task:changed",
  getEventLogs: "debug:get-event-logs",
  getMemoryDebugSnapshot: "debug:get-memory-snapshot"
} as const;

export interface EventLogRecord {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface MemoryDebugSnapshot {
  generatedAt: string;
  nodes: MemoryNode[];
  edges: MemoryEdge[];
  summaries: MemorySummary[];
  recentEvents: EventLogRecord[];
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
  onChatMessageCreated(listener: (message: Message) => void): () => void;
  onTaskChanged(listener: (task: Task) => void): () => void;
  sendChatMessage(input: SendChatMessageInput): Promise<SendChatMessageResult>;
  getEventLogs(): Promise<EventLogRecord[]>;
  getMemoryDebugSnapshot(): Promise<MemoryDebugSnapshot>;
}
