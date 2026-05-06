import { create } from "zustand";
import type { AgentStatus, AppInitialState, SendChatMessageResult } from "../../../shared/contracts/chat";
import type { MemoryDebugSnapshot, PetCursorPosition } from "../../../shared/contracts/ipc";
import type { PetActivity } from "../../../shared/contracts/pet";
import type {
  ModelRoleKey,
  ModelProviderSettingsPatch,
  ProactiveSettingsPatch,
  PublicAppConfig,
  PublicModelHealthMap,
  TaskRunnerSettingsPatch
} from "../../../shared/contracts/settings";
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
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  petActivity?: PetActivity;
  petActivityQueue: PetActivity[];
  petCursorPosition?: PetCursorPosition;
  memoryDebugSnapshot?: MemoryDebugSnapshot;
  debugPetOverlay: boolean;
  loadInitialState(): Promise<void>;
  loadMemoryDebugSnapshot(): Promise<void>;
  subscribeToPetEvents(): () => void;
  openConsoleWindow(): Promise<void>;
  openSettingsWindow(): Promise<void>;
  openConfigFile(): Promise<void>;
  updateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]): Promise<void>;
  updateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<void>;
  testModelConnection(role: ModelRoleKey): Promise<void>;
  updateProactiveSettings(patch: ProactiveSettingsPatch): Promise<void>;
  updatePetSettings(patch: PetSettingsPatch): Promise<void>;
  setPetMousePassthrough(enabled: boolean): Promise<void>;
  movePetWindowBy(delta: { x: number; y: number }): Promise<void>;
  sendMessage(content: string, source?: "console" | "pet_voice" | "pet_text"): Promise<void>;
  cancelTask(taskId: string): Promise<void>;
  deleteTask(taskId: string): Promise<void>;
  showPetNotice(message: string): void;
  clearPetActivity(id?: string): void;
}

export const useAuraBotStore = create<AuraBotStore>((set, get) => ({
  messages: [],
  tasks: [],
  agentStatus: "idle",
  petSettings: { ...DEFAULT_PET_SETTINGS },
  petActivityQueue: [],
  debugPetOverlay: false,
  async loadInitialState() {
    const state: AppInitialState = await window.aurabot.getInitialState();
    set(state);
  },
  async loadMemoryDebugSnapshot() {
    const memoryDebugSnapshot = await window.aurabot.getMemoryDebugSnapshot();
    set({ memoryDebugSnapshot });
  },
  subscribeToPetEvents() {
    const unsubscribeSettings = window.aurabot.onPetSettingsChanged((petSettings) => {
      set({ petSettings });
    });
    const unsubscribeActivity = window.aurabot.onPetActivity((petActivity) => {
      set((current) => enqueuePetActivity(current, petActivity));
    });
    const unsubscribePetCursorPosition = window.aurabot.onPetCursorPosition((petCursorPosition) => {
      set({ petCursorPosition });
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
    const unsubscribeChatMessageUpdated = window.aurabot.onChatMessageUpdated((message) => {
      set((current) => ({
        messages: upsertMessage(current.messages, message)
      }));
    });
    const unsubscribeTaskChanged = window.aurabot.onTaskChanged((task) => {
      set((current) => ({
        tasks: updateTasks(current.tasks, task)
      }));
    });
    const unsubscribeTaskDeleted = window.aurabot.onTaskDeleted((taskId) => {
      set((current) => ({
        tasks: removeTask(current.tasks, taskId)
      }));
    });

    return () => {
      unsubscribeSettings();
      unsubscribeActivity();
      unsubscribePetCursorPosition();
      unsubscribeAgentStatus();
      unsubscribeChatMessage();
      unsubscribeChatMessageUpdated();
      unsubscribeTaskChanged();
      unsubscribeTaskDeleted();
    };
  },
  async openConsoleWindow() {
    await window.aurabot.openConsoleWindow();
  },
  async openSettingsWindow() {
    await window.aurabot.openSettingsWindow();
  },
  async openConfigFile() {
    await window.aurabot.openConfigFile();
  },
  async updateTaskRunnerSettings(runners) {
    const appConfig = await window.aurabot.updateTaskRunnerSettings({ runners });
    set({ appConfig });
  },
  async updateModelProviderSettings(patch) {
    const appConfig = await window.aurabot.updateModelProviderSettings(patch);
    const modelHealth = await window.aurabot.getModelHealth();
    set({ appConfig, modelHealth });
  },
  async testModelConnection(role) {
    set((current) => ({
      modelHealth: current.modelHealth
        ? {
            ...current.modelHealth,
            [role]: {
              ...current.modelHealth[role],
              state: "testing"
            }
          }
        : current.modelHealth
    }));
    const health = await window.aurabot.testModelConnection({ role });
    set((current) => ({
      modelHealth: current.modelHealth
        ? {
            ...current.modelHealth,
            [role]: health
          }
        : current.modelHealth
    }));
  },
  async updateProactiveSettings(patch) {
    const appConfig = await window.aurabot.updateProactiveSettings(patch);
    set({ appConfig });
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
  async sendMessage(content: string, source: "console" | "pet_voice" | "pet_text" = "console") {
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
  async cancelTask(taskId: string) {
    const result = await window.aurabot.cancelTask({ taskId });

    if (!result.ok || !result.task) {
      throw new Error(result.error ?? "任务取消失败。");
    }

    set((current) => ({
      tasks: updateTasks(current.tasks, result.task)
    }));
  },
  async deleteTask(taskId: string) {
    const result = await window.aurabot.deleteTask({ taskId });

    if (!result.ok) {
      throw new Error(result.error ?? "任务删除失败。");
    }

    set((current) => ({
      tasks: removeTask(current.tasks, result.taskId ?? taskId)
    }));
  },
  showPetNotice(message: string) {
    set((current) =>
      enqueuePetActivity(current, {
        id: `local-${Date.now()}`,
        kind: "voice_notice",
        message,
        createdAt: new Date().toISOString()
      })
    );
  },
  clearPetActivity(id?: string) {
    set((current) => {
      if (id && current.petActivity?.id !== id) {
        return {};
      }

      const [nextActivity, ...remainingActivities] = current.petActivityQueue;

      return {
        petActivity: nextActivity,
        petActivityQueue: remainingActivities
      };
    });
  }
}));

function enqueuePetActivity(
  current: Pick<AuraBotStore, "petActivity" | "petActivityQueue">,
  petActivity: PetActivity
): Partial<AuraBotStore> {
  if (!current.petActivity) {
    return { petActivity };
  }

  if (isPriorityPetActivity(petActivity) && !isPriorityPetActivity(current.petActivity)) {
    return {
      petActivity,
      petActivityQueue: current.petActivityQueue.filter(isPriorityPetActivity)
    };
  }

  if (
    current.petActivity.id === petActivity.id ||
    current.petActivityQueue.some((queuedActivity) => queuedActivity.id === petActivity.id)
  ) {
    return {};
  }

  return {
    petActivityQueue: [...current.petActivityQueue, petActivity]
  };
}

function isPriorityPetActivity(petActivity: PetActivity): boolean {
  return petActivity.kind !== "chat_reply";
}

function removeTask(tasks: Task[], taskId: string): Task[] {
  return tasks.filter((task) => task.id !== taskId);
}

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

function upsertMessage(messages: Message[], changedMessage: Message): Message[] {
  const exists = messages.some((message) => message.id === changedMessage.id);

  if (!exists) {
    return [...messages, changedMessage];
  }

  return messages.map((message) => (message.id === changedMessage.id ? changedMessage : message));
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
