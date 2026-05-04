import { create } from "zustand";
import type { AgentStatus, AppInitialState, SendChatMessageResult } from "../../../shared/contracts/chat";
import type { PetActivity } from "../../../shared/contracts/pet";
import type { Message } from "../../../shared/schemas/message";
import { DEFAULT_PET_SETTINGS, type PetSettings, type PetSettingsPatch } from "../../../shared/schemas/petSettings";
import type { Profile } from "../../../shared/schemas/profile";
import type { Task } from "../../../shared/schemas/task";

interface AuraBotStore {
  profile?: Profile;
  mainSessionId?: string;
  messages: Message[];
  tasks: Task[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  petActivity?: PetActivity;
  debugPetOverlay: boolean;
  loadInitialState(): Promise<void>;
  subscribeToPetEvents(): () => void;
  openConsoleWindow(): Promise<void>;
  updatePetSettings(patch: PetSettingsPatch): Promise<void>;
  setPetMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  sendMessage(content: string, source?: "console" | "pet_voice"): Promise<void>;
  showPetNotice(message: string): void;
  clearPetActivity(id?: string): void;
}

export const useAuraBotStore = create<AuraBotStore>((set, get) => ({
  messages: [],
  tasks: [],
  agentStatus: "idle",
  petSettings: { ...DEFAULT_PET_SETTINGS },
  debugPetOverlay: false,
  async loadInitialState() {
    const state: AppInitialState = await window.aurabot.getInitialState();
    set(state);
  },
  subscribeToPetEvents() {
    const unsubscribeSettings = window.aurabot.onPetSettingsChanged((petSettings) => {
      set({ petSettings });
    });
    const unsubscribeActivity = window.aurabot.onPetActivity((petActivity) => {
      set({ petActivity });
    });
    const unsubscribeAgentStatus = window.aurabot.onAgentStatusChanged((agentStatus) => {
      set({ agentStatus });
    });
    const unsubscribeChatMessage = window.aurabot.onChatMessageCreated((message) => {
      set((current) => {
        if (current.messages.some((existingMessage) => existingMessage.id === message.id)) {
          return {};
        }

        return { messages: [...current.messages, message] };
      });
    });
    const unsubscribeTaskChanged = window.aurabot.onTaskChanged((task) => {
      set((current) => ({
        tasks: updateTasks(current.tasks, task)
      }));
    });

    return () => {
      unsubscribeSettings();
      unsubscribeActivity();
      unsubscribeAgentStatus();
      unsubscribeChatMessage();
      unsubscribeTaskChanged();
    };
  },
  async openConsoleWindow() {
    await window.aurabot.openConsoleWindow();
  },
  async updatePetSettings(patch: PetSettingsPatch) {
    const petSettings = await window.aurabot.updatePetSettings(patch);
    set({ petSettings });
  },
  async setPetMousePassthrough(enabled: boolean) {
    await window.aurabot.setPetMousePassthrough(enabled);
  },
  async movePetWindowBy(delta: { x: number; y: number }) {
    await window.aurabot.movePetWindowBy(delta);
  },
  async sendMessage(content: string, source: "console" | "pet_voice" = "console") {
    const trimmed = content.trim();

    if (!trimmed) {
      return;
    }

    const { mainSessionId, profile } = get();
    const pendingUserMessage: Message = {
      id: `local-pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      sessionId: mainSessionId ?? "main-session-pending",
      profileId: profile?.id ?? "profile-pending",
      role: "user",
      content: trimmed,
      createdAt: new Date().toISOString()
    };

    set((current) => ({
      agentStatus: "thinking",
      messages: [...current.messages, pendingUserMessage]
    }));

    try {
      const result = await window.aurabot.sendChatMessage({ content: trimmed, source });

      set((current) => ({
        messages: reconcileMessages(current.messages, pendingUserMessage.id, result),
        tasks: updateTasks(current.tasks, result.createdTask ?? result.updatedTask),
        agentStatus: result.agentStatus
      }));
    } catch (error) {
      set((current) => ({
        messages: current.messages.filter((message) => message.id !== pendingUserMessage.id),
        agentStatus: "idle"
      }));
      throw error;
    }
  },
  showPetNotice(message: string) {
    set({
      petActivity: {
        id: `local-${Date.now()}`,
        kind: "voice_notice",
        message,
        createdAt: new Date().toISOString()
      }
    });
  },
  clearPetActivity(id?: string) {
    set((current) => {
      if (id && current.petActivity?.id !== id) {
        return {};
      }

      return { petActivity: undefined };
    });
  }
}));

function updateTasks(tasks: Task[], changedTask?: Task): Task[] {
  if (!changedTask) {
    return tasks;
  }

  const exists = tasks.some((task) => task.id === changedTask.id);

  if (!exists) {
    return [changedTask, ...tasks];
  }

  return tasks.map((task) => (task.id === changedTask.id ? changedTask : task));
}

function reconcileMessages(messages: Message[], pendingMessageId: string, result: SendChatMessageResult): Message[] {
  const nextMessages = messages.map((message) => (message.id === pendingMessageId ? result.userMessage : message));

  if (!nextMessages.some((message) => message.id === result.userMessage.id)) {
    nextMessages.push(result.userMessage);
  }

  const { assistantMessage } = result;

  if (assistantMessage && !nextMessages.some((message) => message.id === assistantMessage.id)) {
    nextMessages.push(assistantMessage);
  }

  return nextMessages;
}
