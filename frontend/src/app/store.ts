/**
 * 管理 Renderer 可见聊天、Live2D、桌宠与通知状态，不包含 Memory 管理。
 *
 * Store 通过 preload API 初始化和发送普通聊天，并协调 pet activity 队列；它不保存
 * Task 列表/详情/debug，也不订阅或调用任何 Task IPC。
 */

import { defineStore } from "pinia";
import type {
  AgentStatus,
  AppInitialState,
  SendChatMessageResult
} from "@shared/contracts/chat";
import type { Live2DModelState } from "@shared/contracts/live2dModel";
import type { PetCursorPosition } from "@shared/contracts/ipc";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import type { PetActivity } from "@shared/contracts/pet";
import type { Message } from "@shared/schemas/message";
import {
  DEFAULT_PET_SETTINGS,
  type PetSettings
} from "@shared/schemas/petSettings";
import type { Profile } from "@shared/schemas/profile";

const PET_ACTIVITY_TIMEOUT_MS = 7000;
type Unsubscribe = () => void;

interface AuraBotState {
  snapshotRequest:number;
  profileRuntime?:import("@shared/contracts/desktop").ProfileRuntimeState;
  profile?: Profile;
  mainSessionId?: string;
  messages: Message[];
  historyCursor?:number;
  chatError?:string;
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  live2dModelState?: Live2DModelState;
  notificationSettings?: NotificationSettings;
  petActivity?: PetActivity;
  petActivityQueue: PetActivity[];
  petCursorPosition?: PetCursorPosition;
  focusAt: { x: number; y: number };
  debugPetOverlay: boolean;
  isInitialized: boolean;
}

export const useAuraBotStore = defineStore("auraBot", {
  state: (): AuraBotState => ({
    snapshotRequest:0,
    profileRuntime:undefined,
    profile:undefined,
    mainSessionId:undefined,
    live2dModelState:undefined,
    notificationSettings:undefined,
    petActivity:undefined,
    petCursorPosition:undefined,
    messages: [],
    historyCursor: undefined as number|undefined,
    chatError: undefined as string|undefined,
    agentStatus: "idle",
    petSettings: { ...DEFAULT_PET_SETTINGS },
    petActivityQueue: [],
    focusAt: { x: 0, y: 0 },
    debugPetOverlay: false,
    isInitialized: false
  }),
  getters: {
    modelPath: (state): string =>
      state.live2dModelState?.currentModelPath ??
      "",
    displayLanguage: (state): PetSettings["displayLanguage"] =>
      state.petSettings.displayLanguage
  },
  actions: {
    async loadInitialState() {
      const request=++this.snapshotRequest;
      try {
        const state: AppInitialState = await window.aurabot.getInitialState();
        if(request!==this.snapshotRequest)return;
        if(this.profile?.id!==state.profile?.id){this.historyCursor=undefined;this.petActivity=undefined;this.petActivityQueue=[];}
        this.$patch({
          ...state,
          historyCursor:state.historyCursor,
          focusAt: this.petCursorPosition
            ? { x: this.petCursorPosition.localX, y: this.petCursorPosition.localY }
            : this.focusAt
        });
      } finally {
        if(request===this.snapshotRequest)this.isInitialized = true;
      }
    },
    async initialize() {
      await this.loadInitialState();
      const owner=this.profile?.id;const page=await window.aurabot.getHistory(undefined,owner);if(owner!==this.profile?.id)return;this.messages=page.messages;this.historyCursor=page.nextCursor;
    },
    subscribeToPetEvents(): Unsubscribe {
      const unsubscribers: Unsubscribe[] = [
        window.aurabot.onRealtimeError(error=>{this.chatError=error||undefined;}),
        window.aurabot.onPetSettingsChanged((value) => {
          this.petSettings = value;
        }),
        window.aurabot.onPetActivity((value) => this.enqueuePetActivity(value)),
        window.aurabot.onLive2DModelStateChanged((value) => {
          this.live2dModelState = value;
        }),
        window.aurabot.onNotificationSettingsChanged((value) => {
          this.notificationSettings = value;
        }),
        window.aurabot.onPetCursorPosition((value) => {
          this.petCursorPosition = value;
          this.focusAt = { x: value.localX, y: value.localY };
        }),
        window.aurabot.onAgentStatusChanged((value) => {
          this.agentStatus = value;
        }),
        window.aurabot.onChatMessageCreated((value) => {
          if(value.profileId!==this.profile?.id)return;
          if (!this.messages.some((message) => message.id === value.id)) {
            this.messages = [...this.messages, value];
          }
        }),
        window.aurabot.onChatMessageUpdated((value) => {
          if(value.profileId!==this.profile?.id)return;
          this.messages = upsertMessage(this.messages, value);
        })
      ];
      if(window.aurabot.onProfilesChanged)unsubscribers.push(window.aurabot.onProfilesChanged(value=>{if(value.selectedId!==this.profile?.id||value.generation!==this.profileRuntime?.generation||value.revision!==this.profileRuntime?.revision)void this.loadInitialState();else this.profileRuntime=value;}));
      return () => unsubscribers.forEach((unsubscribe) => unsubscribe());
    },
    async loadOlderHistory(){
      if(!this.historyCursor)return;const owner=this.profile?.id;const page=await window.aurabot.getHistory(this.historyCursor,owner);
      if(owner!==this.profile?.id)return;
      this.messages=[...page.messages.filter(m=>!this.messages.some(old=>old.id===m.id)),...this.messages];this.historyCursor=page.nextCursor;
    },
    async openConsoleWindow() {
      await window.aurabot.openConsoleWindow();
    },
    async openSettingsWindow() {
      await window.aurabot.openSettingsWindow();
    },

    async setPetMousePassthrough(enabled: boolean) {
      await window.aurabot.setPetMousePassthrough(enabled);
    },
    async movePetWindowBy(delta: { x: number; y: number }) {
      await window.aurabot.movePetWindowBy(delta);
    },
    async sendMessage(
      content: string,
      source: "console" | "pet_voice" | "pet_text" = "console"
    ) {
      const trimmed = content.trim();
      if (!trimmed) {
        return;
      }
      const pending: Message = {
        id: `local-pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        sessionId: this.mainSessionId ?? "main-session-pending",
        profileId: this.profile?.id ?? "profile-pending",
        role: "user",
        content: trimmed,
        createdAt: new Date().toISOString()
      };
      const generation=this.profileRuntime?.generation;
      this.chatError=undefined;
      this.agentStatus = "thinking";
      this.messages = [...this.messages, pending];
      try {
        const result = await window.aurabot.sendChatMessage({
          content: trimmed,
          source
        });
        if(this.profile?.id!==pending.profileId||generation!==this.profileRuntime?.generation)return;
        this.messages = reconcileMessages(this.messages, pending.id, result);
        this.agentStatus = result.agentStatus;
      } catch (error) {
        if(this.profile?.id!==pending.profileId||generation!==this.profileRuntime?.generation)return;
        this.messages = this.messages.filter((message) => message.id !== pending.id);
        this.agentStatus = "idle";
        this.chatError=error instanceof Error?error.message:"发送失败";
        throw error;
      }
    },
    showPetNotice(message: string) {
      this.enqueuePetActivity({
        id: `local-${Date.now()}`,
        kind: "voice_notice",
        message,
        createdAt: new Date().toISOString()
      });
    },
    enqueuePetActivity(activity: PetActivity) {
      if (!this.petActivity) {
        this.petActivity = activity;
      } else if (
        this.petActivity.id !== activity.id &&
        !this.petActivityQueue.some((queued) => queued.id === activity.id)
      ) {
        if (isPriorityPetActivity(activity) && !isPriorityPetActivity(this.petActivity)) {
          this.petActivity = activity;
          this.petActivityQueue = this.petActivityQueue.filter(isPriorityPetActivity);
        } else {
          this.petActivityQueue = [...this.petActivityQueue, activity];
        }
      }
    },
    clearPetActivity(id?: string) {
      if (id && this.petActivity?.id !== id) {
        return;
      }
      const [next, ...remaining] = this.petActivityQueue;
      this.petActivity = next;
      this.petActivityQueue = remaining;
    },
    armPetActivityAutoClear(activityId: string) {
      window.setTimeout(() => this.clearPetActivity(activityId), PET_ACTIVITY_TIMEOUT_MS);
    }
  }
});

function isPriorityPetActivity(activity: PetActivity): boolean {
  return activity.kind !== "chat_reply";
}

function upsertMessage(messages: Message[], changed: Message): Message[] {
  return messages.some((message) => message.id === changed.id)
    ? messages.map((message) => (message.id === changed.id ? changed : message))
    : [...messages, changed];
}

function reconcileMessages(
  messages: Message[],
  pendingId: string,
  result: SendChatMessageResult
): Message[] {
  const next = messages.map((message) =>
    message.id === pendingId ? result.userMessage : message
  );
  if (!next.some((message) => message.id === result.userMessage.id)) {
    next.push(result.userMessage);
  }
  if (
    result.assistantMessage &&
    !next.some((message) => message.id === result.assistantMessage?.id)
  ) {
    next.push(result.assistantMessage);
  }
  return next.filter((message,index)=>next.findIndex(item=>item.id===message.id)===index);
}
