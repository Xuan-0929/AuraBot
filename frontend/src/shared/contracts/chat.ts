/**
 * 定义普通主聊天在 Main、Preload 与 Renderer 之间共享的公共契约。
 *
 * 正式 UI 只投影已交付消息、连接错误与桌面显示状态；Backend 独占业务生命周期。
 * 历史 Gate 类型仅供未接入启动流程的旧模块编译，不用于正式聊天编排。
 */

import type { Message } from "../schemas/message";
import type { Live2DModelState } from "./live2dModel";
import type { NotificationSettings } from "./notificationSettings";
import type { PetSettings } from "../schemas/petSettings";
import type { Profile } from "../schemas/profile";
import type { PublicAppConfig, PublicModelHealthMap } from "./settings";

export type AgentStatus = "idle" | "listening" | "thinking" | "typing" | "speaking";
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
  chatError?:string;
  profile: Profile;
  mainSessionId: string;
  messages: Message[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  live2dModelState: Live2DModelState;
  notificationSettings: NotificationSettings;
  debugPetOverlay: boolean;
}
