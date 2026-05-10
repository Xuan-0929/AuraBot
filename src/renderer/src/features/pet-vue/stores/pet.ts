import { defineStore } from "pinia";
import type { AgentStatus, AppInitialState, SendChatMessageResult } from "../../../../../shared/contracts/chat";
import type { Live2DModelState } from "../../../../../shared/contracts/live2dModel";
import type { PetCursorPosition } from "../../../../../shared/contracts/ipc";
import type { PetActivity } from "../../../../../shared/contracts/pet";
import { DEFAULT_PET_SETTINGS, type PetSettings } from "../../../../../shared/schemas/petSettings";
import type { Task } from "../../../../../shared/schemas/task";

const PET_ACTIVITY_TIMEOUT_MS = 7000;

interface FocusPoint {
  x: number;
  y: number;
}

interface PetState {
  tasks: Task[];
  agentStatus: AgentStatus;
  petSettings: PetSettings;
  live2dModelState?: Live2DModelState;
  petCursorPosition?: PetCursorPosition;
  petActivity?: PetActivity;
  petActivityQueue: PetActivity[];
  focusAt: FocusPoint;
}

type Unsubscribe = () => void;

export const usePetStore = defineStore("pet", {
  state: (): PetState => ({
    tasks: [],
    agentStatus: "idle",
    petSettings: { ...DEFAULT_PET_SETTINGS },
    live2dModelState: undefined,
    petCursorPosition: undefined,
    petActivity: undefined,
    petActivityQueue: [],
    focusAt: { x: 0, y: 0 }
  }),
  getters: {
    modelPath: (state): string => state.live2dModelState?.currentModelPath ?? "assets/live2d/hiyori/Hiyori.model3.json",
    displayLanguage: (state): PetSettings["displayLanguage"] => state.petSettings.displayLanguage
  },
  actions: {
    async initialize() {
      const initialState: AppInitialState = await window.aurabot.getInitialState();
      this.tasks = initialState.tasks;
      this.agentStatus = initialState.agentStatus;
      this.petSettings = initialState.petSettings;
      this.live2dModelState = initialState.live2dModelState;
    },
    subscribeToIpc(): Unsubscribe {
      const unsubscribers: Unsubscribe[] = [];
      unsubscribers.push(
        window.aurabot.onPetSettingsChanged((petSettings) => {
          this.petSettings = petSettings;
        })
      );
      unsubscribers.push(
        window.aurabot.onLive2DModelStateChanged((live2dModelState) => {
          this.live2dModelState = live2dModelState;
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
          this.agentStatus = agentStatus;
        })
      );
      unsubscribers.push(
        window.aurabot.onTaskChanged((task) => {
          const exists = this.tasks.some((currentTask) => currentTask.id === task.id);
          this.tasks = exists ? this.tasks.map((currentTask) => (currentTask.id === task.id ? task : currentTask)) : [task, ...this.tasks];
        })
      );
      unsubscribers.push(
        window.aurabot.onTaskDeleted((taskId) => {
          this.tasks = this.tasks.filter((task) => task.id !== taskId);
        })
      );
      unsubscribers.push(
        window.aurabot.onPetActivity((petActivity) => {
          this.enqueuePetActivity(petActivity);
        })
      );

      return () => {
        for (const unsubscribe of unsubscribers) {
          unsubscribe();
        }
      };
    },
    enqueuePetActivity(petActivity: PetActivity) {
      if (!this.petActivity) {
        this.petActivity = petActivity;
        return;
      }

      if (this.petActivity.id === petActivity.id || this.petActivityQueue.some((queued) => queued.id === petActivity.id)) {
        return;
      }

      if (isPriorityPetActivity(petActivity) && !isPriorityPetActivity(this.petActivity)) {
        this.petActivity = petActivity;
        this.petActivityQueue = this.petActivityQueue.filter(isPriorityPetActivity);
        return;
      }

      this.petActivityQueue.push(petActivity);
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
    },
    async setPetMousePassthrough(enabled: boolean) {
      await window.aurabot.setPetMousePassthrough(enabled);
    },
    async openConsoleWindow() {
      await window.aurabot.openConsoleWindow();
    },
    async sendTextInput(content: string) {
      const trimmed = content.trim();
      if (!trimmed) {
        return;
      }
      const result: SendChatMessageResult = await window.aurabot.sendChatMessage({
        content: trimmed,
        source: "pet_text"
      });
      this.agentStatus = result.agentStatus;
      if (result.createdTask) {
        this.tasks = [result.createdTask, ...this.tasks.filter((task) => task.id !== result.createdTask?.id)];
      }
      if (result.updatedTask) {
        this.tasks = this.tasks.map((task) => (task.id === result.updatedTask?.id ? result.updatedTask : task));
      }
    }
  }
});

function isPriorityPetActivity(activity: PetActivity): boolean {
  return activity.kind !== "chat_reply";
}
