import type { AppInitialState, SendChatMessageInput, SendChatMessageResult } from "./chat";

export const IPC_CHANNELS = {
  getInitialState: "app:get-initial-state",
  openConsoleWindow: "window:open-console",
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
  sendChatMessage(input: SendChatMessageInput): Promise<SendChatMessageResult>;
  getEventLogs(): Promise<EventLogRecord[]>;
}
