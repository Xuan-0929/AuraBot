import type { AppInitialState, SendChatMessageInput, SendChatMessageResult } from "./chat";
import type { Live2DModelState } from "./live2dModel";
import type { NotificationSettings } from "./notificationSettings";
import type { PetActivity } from "./pet";
import type { TaskDetail } from "./taskDetail";
import type { Message } from "../schemas/message";
import type { MemoryEdge, MemoryNode, MemorySummary } from "../schemas/memory";
import type { PetSettings, PetSettingsPatch } from "../schemas/petSettings";
import type { PersonaSettings } from "./personaSettings";
import type { Profile } from "../schemas/profile";
import type { Task } from "../schemas/task";
import type {
  ModelConnectionTestInput,
  ModelProviderSettingsPatch,
  ProactiveSettingsPatch,
  PublicAppConfig,
  PublicModelHealth,
  PublicModelHealthMap,
  TaskRunnerSettingsPatch
} from "./settings";

export const IPC_CHANNELS = {
  getInitialState: "app:get-initial-state",
  openConsoleWindow: "window:open-console",
  openSettingsWindow: "window:open-settings",
  openConfigFile: "config:open-file",
  updateTaskRunnerSettings: "config:update-task-runner-settings",
  updateModelProviderSettings: "config:update-model-provider-settings",
  getModelHealth: "config:get-model-health",
  testModelConnection: "config:test-model-connection",
  updateProactiveSettings: "config:update-proactive-settings",
  updatePersonaSettings: "config:update-persona-settings",
  getLive2DModelState: "live2d:get-model-state",
  setCurrentLive2DModel: "live2d:set-current-model",
  getNotificationSettings: "notifications:get-settings",
  updateNotificationSettings: "notifications:update-settings",
  getPetSettings: "pet:get-settings",
  updatePetSettings: "pet:update-settings",
  setPetMousePassthrough: "pet:set-mouse-passthrough",
  movePetWindowBy: "pet:move-window-by",
  resizePetWindowCorner: "pet:resize-window-corner",
  petSettingsChanged: "pet:settings-changed",
  petActivity: "pet:activity",
  live2dModelStateChanged: "live2d:model-state-changed",
  notificationSettingsChanged: "notifications:settings-changed",
  petCursorPosition: "pet:cursor-position",
  agentStatusChanged: "agent:status-changed",
  chatMessageCreated: "chat:message-created",
  chatMessageUpdated: "chat:message-updated",
  sendChatMessage: "chat:send-message",
  taskChanged: "task:changed",
  taskDeleted: "task:deleted",
  cancelTask: "task:cancel",
  deleteTask: "task:delete",
  openTaskResultFile: "task:open-result-file",
  showTaskResultFileInFolder: "task:show-result-file-in-folder",
  getTaskDetail: "task:get-detail",
  getEventLogs: "debug:get-event-logs",
  getMemoryDebugSnapshot: "debug:get-memory-snapshot"
} as const;

export interface EventLogRecord {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export interface MemoryRecallDebugTrace {
  query: string;
  recallPlan: {
    queryTerms: string[];
    targetTypes: MemoryNode["type"][];
    rankedNodeIds: string[];
  };
  seedNodeIds: string[];
  expandedNodeIds: string[];
  returnedNodeIds: string[];
  traversedEdges: Array<{
    sourceNodeId: string;
    targetNodeId: string;
    relationType?: MemoryEdge["relationType"] | string;
    weight?: number;
    depth: number;
  }>;
}

export interface MemoryDebugSnapshot {
  generatedAt: string;
  nodes: MemoryNode[];
  edges: MemoryEdge[];
  summaries: MemorySummary[];
  recentEvents: EventLogRecord[];
  lastRecallTrace?: MemoryRecallDebugTrace;
}

export interface TaskResultFileActionInput {
  taskId: string;
  path: string;
}

export interface TaskDetailInput {
  taskId: string;
}

export interface PetCursorPosition {
  screenX: number;
  screenY: number;
  localX: number;
  localY: number;
}

/** Corner being dragged to resize the frameless pet window (programmatic resize). */
export type PetResizeCorner = "nw" | "ne" | "sw" | "se";

export interface TaskResultFileActionResult {
  ok: boolean;
  error?: string;
}

export interface TaskControlActionInput {
  taskId: string;
}

export interface TaskControlActionResult {
  ok: boolean;
  task?: Task;
  taskId?: string;
  error?: string;
}

export interface AuraBotApi {
  getInitialState(): Promise<AppInitialState>;
  openConsoleWindow(): Promise<void>;
  openSettingsWindow(): Promise<void>;
  openConfigFile(): Promise<void>;
  updateTaskRunnerSettings(patch: TaskRunnerSettingsPatch): Promise<PublicAppConfig>;
  updateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<PublicAppConfig>;
  getModelHealth(): Promise<PublicModelHealthMap>;
  testModelConnection(input: ModelConnectionTestInput): Promise<PublicModelHealth>;
  updateProactiveSettings(patch: ProactiveSettingsPatch): Promise<PublicAppConfig>;
  updatePersonaSettings(settings: PersonaSettings): Promise<Profile>;
  getLive2DModelState(): Promise<Live2DModelState>;
  setCurrentLive2DModel(modelId: string): Promise<Live2DModelState>;
  getNotificationSettings(): Promise<NotificationSettings>;
  updateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, "permissionState">>
  ): Promise<NotificationSettings>;
  getPetSettings(): Promise<PetSettings>;
  updatePetSettings(patch: PetSettingsPatch): Promise<PetSettings>;
  setPetMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  resizePetWindowCorner(corner: PetResizeCorner, movement: { x: number; y: number }): Promise<void>;
  onPetSettingsChanged(listener: (settings: PetSettings) => void): () => void;
  onPetActivity(listener: (activity: PetActivity) => void): () => void;
  onLive2DModelStateChanged(listener: (state: Live2DModelState) => void): () => void;
  onNotificationSettingsChanged(listener: (settings: NotificationSettings) => void): () => void;
  onPetCursorPosition(listener: (position: PetCursorPosition) => void): () => void;
  onAgentStatusChanged(listener: (status: import("./chat").AgentStatus) => void): () => void;
  onChatMessageCreated(listener: (message: Message) => void): () => void;
  onChatMessageUpdated(listener: (message: Message) => void): () => void;
  onTaskChanged(listener: (task: Task) => void): () => void;
  onTaskDeleted(listener: (taskId: string) => void): () => void;
  sendChatMessage(input: SendChatMessageInput): Promise<SendChatMessageResult>;
  cancelTask(input: TaskControlActionInput): Promise<TaskControlActionResult>;
  deleteTask(input: TaskControlActionInput): Promise<TaskControlActionResult>;
  openTaskResultFile(input: TaskResultFileActionInput): Promise<TaskResultFileActionResult>;
  showTaskResultFileInFolder(input: TaskResultFileActionInput): Promise<TaskResultFileActionResult>;
  getTaskDetail(input: TaskDetailInput): Promise<TaskDetail | undefined>;
  getEventLogs(): Promise<EventLogRecord[]>;
  getMemoryDebugSnapshot(): Promise<MemoryDebugSnapshot>;
}
