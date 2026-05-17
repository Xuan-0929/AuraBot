import { defineStore } from "pinia";
import type {
  AgentStatus,
  AppInitialState,
  ChatInputAttachment,
  SendChatMessageResult
} from "../../../shared/contracts/chat";
import type { Live2DModelState } from "../../../shared/contracts/live2dModel";
import type { MemoryDebugSnapshot, PetCursorPosition } from "../../../shared/contracts/ipc";
import type { NotificationSettings } from "../../../shared/contracts/notificationSettings";
import type { PetActivity } from "../../../shared/contracts/pet";
import type { TaskDetail } from "../../../shared/contracts/taskDetail";
import type {
  ModelProviderSettingsPatch,
  ModelRoleKey,
  ProactiveSettingsPatch,
  PublicAppConfig,
  PublicModelHealthMap,
  TaskRunnerSettingsPatch
} from "../../../shared/contracts/settings";
import type { PersonaSettings } from "../../../shared/contracts/personaSettings";
import type { Message } from "../../../shared/schemas/message";
import { DEFAULT_PET_SETTINGS, type PetSettings, type PetSettingsPatch } from "../../../shared/schemas/petSettings";
import type { Profile } from "../../../shared/schemas/profile";
import type { Task } from "../../../shared/schemas/task";

const PET_ACTIVITY_TIMEOUT_MS = 7000;

interface FocusPoint {
  x: number;
  y: number;
}

interface AuraBotState {
  profile?: Profile;
  mainSessionId?: string;
  messages: Message[];
  tasks: Task[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  appConfig?: PublicAppConfig;
  modelHealth?: PublicModelHealthMap;
  live2dModelState?: Live2DModelState;
  notificationSettings?: NotificationSettings;
  petActivity?: PetActivity;
  petActivityQueue: PetActivity[];
  petCursorPosition?: PetCursorPosition;
  focusAt: FocusPoint;
  memoryDebugSnapshot?: MemoryDebugSnapshot;
  selectedTaskDetail?: TaskDetail;
  selectedTaskDetailTaskId?: string;
  isTaskDetailLoading: boolean;
  debugPetOverlay: boolean;
  isInitialized: boolean;
}

type Unsubscribe = () => void;

export const useAuraBotStore = defineStore("auraBot", {
  state: (): AuraBotState => ({
    profile: undefined,
    mainSessionId: undefined,
    messages: [],
    tasks: [],
    agentStatus: "idle",
    petSettings: { ...DEFAULT_PET_SETTINGS },
    appConfig: undefined,
    modelHealth: undefined,
    live2dModelState: undefined,
    notificationSettings: undefined,
    petActivity: undefined,
    petActivityQueue: [],
    petCursorPosition: undefined,
    focusAt: { x: 0, y: 0 },
    memoryDebugSnapshot: undefined,
    selectedTaskDetail: undefined,
    selectedTaskDetailTaskId: undefined,
    isTaskDetailLoading: false,
    debugPetOverlay: false,
    isInitialized: false
  }),
  getters: {
    modelPath: (state): string => state.live2dModelState?.currentModelPath ?? "assets/live2d/hiyori/Hiyori.model3.json",
    displayLanguage: (state): PetSettings["displayLanguage"] => state.petSettings.displayLanguage
  },
  actions: {
    async loadInitialState() {
      try {
        const state: AppInitialState = await window.aurabot.getInitialState();
        this.$patch({
          ...state,
          focusAt: this.petCursorPosition
            ? { x: this.petCursorPosition.localX, y: this.petCursorPosition.localY }
            : this.focusAt
        });
      } finally {
        this.isInitialized = true;
      }
    },
    async initialize() {
      await this.loadInitialState();
    },
    async loadMemoryDebugSnapshot() {
      this.memoryDebugSnapshot = await window.aurabot.getMemoryDebugSnapshot();
    },
    subscribeToPetEvents(): Unsubscribe {
      const unsubscribers: Unsubscribe[] = [];

      unsubscribers.push(
        window.aurabot.onPetSettingsChanged((petSettings) => {
          this.petSettings = petSettings;
        })
      );
      unsubscribers.push(
        window.aurabot.onPetActivity((petActivity) => {
          this.enqueuePetActivity(petActivity);
        })
      );
      unsubscribers.push(
        window.aurabot.onLive2DModelStateChanged((live2dModelState) => {
          this.live2dModelState = live2dModelState;
        })
      );
      unsubscribers.push(
        window.aurabot.onNotificationSettingsChanged((notificationSettings) => {
          this.notificationSettings = notificationSettings;
        })
      );
      unsubscribers.push(
        window.aurabot.onPetCursorPosition((petCursorPosition) => {
          this.petCursorPosition = petCursorPosition;
          this.focusAt = { x: petCursorPosition.localX, y: petCursorPosition.localY };
        })
      );
      unsubscribers.push(
        window.aurabot.onAgentStatusChanged((agentStatus) => {
          if (this.agentStatus !== agentStatus) {
            this.agentStatus = agentStatus;
          }
        })
      );
      unsubscribers.push(
        window.aurabot.onChatMessageCreated((message) => {
          if (this.messages.some((existingMessage) => existingMessage.id === message.id)) {
            return;
          }
          this.messages = [...this.messages, message];
        })
      );
      unsubscribers.push(
        window.aurabot.onChatMessageUpdated((message) => {
          this.messages = upsertMessage(this.messages, message);
        })
      );
      unsubscribers.push(
        window.aurabot.onTaskChanged((task) => {
          this.tasks = updateTasks(this.tasks, task);
          if (this.selectedTaskDetailTaskId === task.id) {
            void this.openTaskDetail(task.id);
          }
        })
      );
      unsubscribers.push(
        window.aurabot.onTaskDeleted((taskId) => {
          this.tasks = removeTask(this.tasks, taskId);
          if (this.selectedTaskDetailTaskId === taskId) {
            this.closeTaskDetail();
          }
        })
      );

      return () => {
        for (const unsubscribe of unsubscribers) {
          unsubscribe();
        }
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
    async updateTaskRunnerSettings(runners: TaskRunnerSettingsPatch["runners"]) {
      this.appConfig = await window.aurabot.updateTaskRunnerSettings({ runners });
    },
    async updateModelProviderSettings(patch: ModelProviderSettingsPatch) {
      this.appConfig = await window.aurabot.updateModelProviderSettings(patch);
      this.modelHealth = await window.aurabot.getModelHealth();
    },
    async testModelConnection(role: ModelRoleKey) {
      if (this.modelHealth) {
        this.modelHealth = {
          ...this.modelHealth,
          [role]: {
            ...this.modelHealth[role],
            state: "testing"
          }
        };
      }

      const health = await window.aurabot.testModelConnection({ role });
      if (this.modelHealth) {
        this.modelHealth = {
          ...this.modelHealth,
          [role]: health
        };
      }
    },
    async updateProactiveSettings(patch: ProactiveSettingsPatch) {
      this.appConfig = await window.aurabot.updateProactiveSettings(patch);
    },
    async updatePersonaSettings(settings: PersonaSettings) {
      this.profile = await window.aurabot.updatePersonaSettings(settings);
    },
    async setCurrentLive2DModel(modelId: string) {
      this.live2dModelState = await window.aurabot.setCurrentLive2DModel(modelId);
    },
    async updateNotificationSettings(patch: Partial<Omit<NotificationSettings, "permissionState">>) {
      this.notificationSettings = await window.aurabot.updateNotificationSettings(patch);
    },
    async updatePetSettings(patch: PetSettingsPatch) {
      this.petSettings = await window.aurabot.updatePetSettings(patch);
    },
    async setPetMousePassthrough(enabled: boolean) {
      await window.aurabot.setPetMousePassthrough(enabled);
    },
    async movePetWindowBy(delta: { x: number; y: number }) {
      await window.aurabot.movePetWindowBy(delta);
    },
    async sendMessage(
      content: string,
      source: "console" | "pet_voice" | "pet_text" = "console",
      attachments: ChatInputAttachment[] = []
    ) {
      const trimmed = content.trim();

      if (!trimmed) {
        return;
      }

      const pendingUserMessage: Message = {
        id: `local-pending-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        sessionId: this.mainSessionId ?? "main-session-pending",
        profileId: this.profile?.id ?? "profile-pending",
        role: "user",
        content: trimmed,
        attachments: attachments.map((attachment, index) => ({
          id: `local-input-attachment-${Date.now()}-${index}`,
          kind: attachment.kind,
          purpose: "input",
          name: attachment.name
        })),
        createdAt: new Date().toISOString()
      };

      this.agentStatus = "thinking";
      this.messages = [...this.messages, pendingUserMessage];

      try {
        const result = await window.aurabot.sendChatMessage({ content: trimmed, source, attachments });
        this.messages = reconcileMessages(this.messages, pendingUserMessage.id, result);
        this.tasks = updateTasks(this.tasks, result.createdTask ?? result.updatedTask);
        if (result.deletedTaskId) {
          this.tasks = removeTask(this.tasks, result.deletedTaskId);
        }
        this.agentStatus = result.agentStatus;
      } catch (error) {
        this.messages = this.messages.filter((message) => message.id !== pendingUserMessage.id);
        this.agentStatus = "idle";
        throw error;
      }
    },
    async cancelTask(taskId: string) {
      const result = await window.aurabot.cancelTask({ taskId });

      if (!result.ok || !result.task) {
        throw new Error(result.error ?? "任务取消失败。");
      }

      this.tasks = updateTasks(this.tasks, result.task);
    },
    async deleteTask(taskId: string) {
      const result = await window.aurabot.deleteTask({ taskId });

      if (!result.ok) {
        throw new Error(result.error ?? "任务删除失败。");
      }

      this.tasks = removeTask(this.tasks, result.taskId ?? taskId);
      if (this.selectedTaskDetailTaskId === taskId) {
        this.closeTaskDetail();
      }
    },
    async openTaskDetail(taskId: string) {
      this.selectedTaskDetailTaskId = taskId;
      this.isTaskDetailLoading = true;

      try {
        const detail = await window.aurabot.getTaskDetail({ taskId });

        if (this.selectedTaskDetailTaskId === taskId) {
          this.selectedTaskDetail = detail;
        }
      } finally {
        if (this.selectedTaskDetailTaskId === taskId) {
          this.isTaskDetailLoading = false;
        }
      }
    },
    closeTaskDetail() {
      this.selectedTaskDetail = undefined;
      this.selectedTaskDetailTaskId = undefined;
      this.isTaskDetailLoading = false;
    },
    showPetNotice(message: string) {
      this.enqueuePetActivity({
        id: `local-${Date.now()}`,
        kind: "voice_notice",
        message,
        createdAt: new Date().toISOString()
      });
    },
    enqueuePetActivity(petActivity: PetActivity) {
      if (!this.petActivity) {
        this.petActivity = petActivity;
        return;
      }

      if (
        this.petActivity.id === petActivity.id ||
        this.petActivityQueue.some((queuedActivity) => queuedActivity.id === petActivity.id)
      ) {
        return;
      }

      if (isPriorityPetActivity(petActivity) && !isPriorityPetActivity(this.petActivity)) {
        this.petActivity = petActivity;
        this.petActivityQueue = this.petActivityQueue.filter(isPriorityPetActivity);
        return;
      }

      this.petActivityQueue = [...this.petActivityQueue, petActivity];
    },
    clearPetActivity(id?: string) {
      if (id && this.petActivity?.id !== id) {
        return;
      }

      const [nextActivity, ...remainingActivities] = this.petActivityQueue;
      this.petActivity = nextActivity;
      this.petActivityQueue = remainingActivities;
    },
    armPetActivityAutoClear(activityId: string) {
      window.setTimeout(() => {
        this.clearPetActivity(activityId);
      }, PET_ACTIVITY_TIMEOUT_MS);
    }
  }
});

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

  if (result.assistantMessage && !nextMessages.some((message) => message.id === result.assistantMessage?.id)) {
    nextMessages.push(result.assistantMessage);
  }

  return nextMessages;
}
