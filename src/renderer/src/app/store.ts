import { create } from "zustand";
import type { AgentStatus, AppInitialState } from "../../../shared/contracts/chat";
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

export const useAuraBotStore = create<AuraBotStore>((set) => ({
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

    return () => {
      unsubscribeSettings();
      unsubscribeActivity();
      unsubscribeAgentStatus();
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

    set({ agentStatus: "thinking" });
    const result = await window.aurabot.sendChatMessage({ content: trimmed, source });

    set((current) => ({
      messages: [
        ...current.messages,
        result.userMessage,
        ...(result.assistantMessage ? [result.assistantMessage] : [])
      ],
      tasks: updateTasks(current.tasks, result.createdTask ?? result.updatedTask),
      agentStatus: result.agentStatus
    }));
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
