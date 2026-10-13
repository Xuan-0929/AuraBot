/** Carries Backend acoustic no_audio/listening state through the existing desktop status flow. */
/**
 * 定义普通主聊天在 Main、Preload 与 Renderer 之间共享的公共契约。
 *
 * 正式 UI 只投影已交付消息、连接错误与桌面显示状态；Backend 独占业务生命周期。
 */

import type { Message } from "../schemas/message";
import type { Live2DModelState } from "./live2dModel";
import type { PetSettings } from "../schemas/petSettings";
import type { Profile } from "../schemas/profile";

export type AgentStatus = "idle" | "no_audio" | "listening" | "thinking" | "typing" | "speaking";
export interface SendChatMessageInput {
  content: string;
  source?: "console" | "pet_voice" | "pet_text";
}

export interface SendChatMessageResult {
  userMessage: Message;
  agentStatus: AgentStatus;
}

export interface AppInitialState {
  historyCursor?:number;
  profileRuntime?:import("./desktop").ProfileRuntimeState;
  chatError?:string;
  profile: Profile;
  mainSessionId: string;
  messages: Message[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  live2dModelState: Live2DModelState;
}
