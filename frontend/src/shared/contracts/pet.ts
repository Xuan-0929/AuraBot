/**
 * 桌宠活动公共契约：描述聊天、语音、Memory 跟进和空闲主动消息。
 * 主进程通知、renderer store 与 Live2D 表现共享这些类型，不承载 Task 生命周期事件。
 */
export type PetActivityKind =
  | "voice_reply"
  | "voice_notice"
  | "memory_follow_up"
  | "chat_reply"
  | "proactive_idle";

export interface PetActivity {
  id: string;
  kind: PetActivityKind;
  message: string;
  createdAt: string;
}
