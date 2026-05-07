import type { Message } from "../schemas/message";
import type { Live2DModelState } from "./live2dModel";
import type { NotificationSettings } from "./notificationSettings";
import type { PetSettings } from "../schemas/petSettings";
import type { Profile } from "../schemas/profile";
import type { Task } from "../schemas/task";
import type { PublicAppConfig, PublicModelHealthMap } from "./settings";
import type { TaskKind } from "../schemas/task";

export type AgentStatus = "idle" | "listening" | "thinking" | "typing" | "tasking";
export type ReplyDecision = "reply" | "wait";
export type InputKind = "chat" | "task" | "task_control";
export type MemoryDecision = "write_now" | "defer" | "skip";

export interface GateDecision {
  replyDecision: ReplyDecision;
  inputKind: InputKind;
  memoryDecision: MemoryDecision;
  confidence: number;
  reason: string;
  coalescedText: string;
  topicEnded: boolean;
  taskKind?: TaskKind;
}

export interface SendChatMessageInput {
  content: string;
  source?: "console" | "pet_voice" | "pet_text";
}

export interface SendChatMessageResult {
  userMessage: Message;
  assistantMessage?: Message;
  agentStatus: AgentStatus;
  gateDecision?: GateDecision;
  createdTask?: Task;
  updatedTask?: Task;
}

export interface AppInitialState {
  profile: Profile;
  mainSessionId: string;
  messages: Message[];
  tasks: Task[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  appConfig: PublicAppConfig;
  modelHealth: PublicModelHealthMap;
  live2dModelState: Live2DModelState;
  notificationSettings: NotificationSettings;
  debugPetOverlay: boolean;
}
