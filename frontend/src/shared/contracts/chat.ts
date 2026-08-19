/**
 * 定义普通主聊天在 Main、Preload 与 Renderer 之间共享的公共契约。
 *
 * Gate 只区分 chat 与最小 task 意图；结果和初始状态只投影消息、Memory 周边设置、
 * Live2D 与模型健康，不再携带旧执行 DTO 或专属执行状态。
 */

import type { Message } from "../schemas/message";
import type { Live2DModelState } from "./live2dModel";
import type { NotificationSettings } from "./notificationSettings";
import type { PetSettings } from "../schemas/petSettings";
import type { Profile } from "../schemas/profile";
import type { PublicAppConfig, PublicModelHealthMap } from "./settings";

export type AgentStatus = "idle" | "listening" | "thinking" | "typing";
export type ReplyDecision = "reply" | "wait";
export type InputKind = "chat" | "task";
export type MemoryDecision = "write_now" | "defer" | "skip";

export interface GateDecision {
  replyDecision: ReplyDecision;
  inputKind: InputKind;
  memoryDecision: MemoryDecision;
  confidence: number;
  reason: string;
  coalescedText: string;
  topicEnded: boolean;
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
}

export interface MainSessionMessagesSnapshot {
  sessionId: string;
  messages: Message[];
}

export interface AppInitialState {
  profile: Profile;
  mainSessionId: string;
  messages: Message[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  appConfig: PublicAppConfig;
  modelHealth: PublicModelHealthMap;
  live2dModelState: Live2DModelState;
  notificationSettings: NotificationSettings;
  debugPetOverlay: boolean;
}
