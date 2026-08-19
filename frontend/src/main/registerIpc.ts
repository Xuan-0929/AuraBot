/**
 * 注册 Electron 当前保留的窗口、聊天、模型、Memory、桌宠与通知 IPC handlers。
 *
 * 初始状态和 debug 数据来自本地非 Task services，Backend 只用于 health；聊天流通过
 * typed broadcasts 投影给 Renderer，所有 Task channels 与文件动作已删除。
 */

import { ipcMain } from "electron";
import type { AppServices } from "@core/bootstrap/createServices";
import type {
  AgentStatus,
  SendChatMessageInput
} from "@shared/contracts/chat";
import type { BackendStatus } from "@shared/contracts/backend";
import type { Live2DModelState } from "@shared/contracts/live2dModel";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import type { PersonaSettings } from "@shared/contracts/personaSettings";
import type { PetActivity } from "@shared/contracts/pet";
import type {
  ModelConnectionTestInput,
  ModelProviderSettingsPatch,
  ModelRoleKey,
  ProactiveSettingsPatch,
  PublicAppConfig,
  PublicModelHealth,
  PublicModelHealthMap
} from "@shared/contracts/settings";
import {
  IPC_CHANNELS,
  type MemoryDebugSnapshot,
  type PetResizeDirection
} from "@shared/contracts/ipc";
import type { Message } from "@shared/schemas/message";
import {
  parsePetSettingsPatch,
  type PetSettings,
  type PetSettingsPatch
} from "@shared/schemas/petSettings";
import type { Profile } from "@shared/schemas/profile";
import { splitTextIntoSentences } from "@shared/text/sentences";
import { createPublicAppConfig } from "@shared/contracts/settings";

export interface WindowController {
  openConsoleWindow(): void;
  openSettingsWindow(): void;
  openConfigFile(): Promise<void>;
  updateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<PublicAppConfig>;
  getModelHealth(): PublicModelHealthMap;
  testModelConnection(role: ModelRoleKey): Promise<PublicModelHealth>;
  updateProactiveSettings(patch: ProactiveSettingsPatch): Promise<PublicAppConfig>;
  getBackendStatus(): Promise<BackendStatus>;
  updatePersonaSettings(settings: PersonaSettings): Profile;
  getLive2DModelState(): Live2DModelState;
  setCurrentLive2DModel(modelId: string): Live2DModelState;
  getNotificationSettings(): NotificationSettings;
  updateNotificationSettings(
    patch: Partial<Omit<NotificationSettings, "permissionState">>
  ): NotificationSettings;
  recordUserActivity(): void;
  getPetSettings(): PetSettings;
  updatePetSettings(patch: PetSettingsPatch): PetSettings;
  setPetMousePassthrough(enabled: boolean): void;
  movePetWindowBy(delta: { x: number; y: number }): void;
  resizePetWindowCorner(
    direction: PetResizeDirection,
    movement: { x: number; y: number }
  ): void;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastAgentStatus(status: AgentStatus): void;
  broadcastChatMessage(message: Message): void;
  broadcastChatMessageUpdated(message: Message): void;
}

export function registerIpc(services: AppServices, windows: WindowController): void {
  const petActivityCursors = new Map<string, number>();
  const broadcastSentences = (message: Message, flushRemainder = false) => {
    const result = buildAssistantMessagePetActivities(message, {
      startIndex: petActivityCursors.get(message.id) ?? 0,
      flushRemainder
    });
    petActivityCursors.set(message.id, result.nextIndex);
    result.activities.forEach((activity) => windows.broadcastPetActivity(activity));
  };

  ipcMain.handle(IPC_CHANNELS.getInitialState, () => {
    const profile = services.profileService.getOrSeedActiveProfile();
    const session = services.sessionRepository.getOrCreateMainSession(profile.id);
    services.eventLogService.record("app.initial_state_loaded", {
      profileId: profile.id,
      sessionId: session.id
    });
    return {
      profile,
      mainSessionId: session.id,
      messages: services.messageRepository.listBySession(session.id),
      agentStatus: "idle",
      petSettings: windows.getPetSettings(),
      appConfig: createPublicAppConfig(services.config),
      modelHealth: windows.getModelHealth(),
      live2dModelState: windows.getLive2DModelState(),
      notificationSettings: windows.getNotificationSettings(),
      debugPetOverlay: process.env.AURABOT_DEBUG_PET === "1"
    };
  });

  ipcMain.handle(IPC_CHANNELS.openConsoleWindow, () => windows.openConsoleWindow());
  ipcMain.handle(IPC_CHANNELS.openSettingsWindow, () => windows.openSettingsWindow());
  ipcMain.handle(IPC_CHANNELS.openConfigFile, () => windows.openConfigFile());
  ipcMain.handle(
    IPC_CHANNELS.updateModelProviderSettings,
    (_event, patch: ModelProviderSettingsPatch) =>
      windows.updateModelProviderSettings(patch)
  );
  ipcMain.handle(IPC_CHANNELS.getModelHealth, () => windows.getModelHealth());
  ipcMain.handle(
    IPC_CHANNELS.testModelConnection,
    async (_event, input: ModelConnectionTestInput) => {
      services.eventLogService.record("model.health.test_requested", {
        role: input.role,
        provider: services.config[input.role].provider
      });
      const health = await windows.testModelConnection(input.role);
      services.eventLogService.record(
        health.state === "connected" ? "model.health.checked" : "model.health.failed",
        {
          role: health.role,
          provider: health.provider,
          state: health.state,
          lastErrorCode: health.lastErrorCode
        }
      );
      return health;
    }
  );
  ipcMain.handle(
    IPC_CHANNELS.updateProactiveSettings,
    (_event, patch: ProactiveSettingsPatch) => windows.updateProactiveSettings(patch)
  );
  ipcMain.handle(IPC_CHANNELS.getBackendStatus, () => windows.getBackendStatus());
  ipcMain.handle(
    IPC_CHANNELS.updatePersonaSettings,
    (_event, settings: PersonaSettings) => windows.updatePersonaSettings(settings)
  );
  ipcMain.handle(IPC_CHANNELS.getLive2DModelState, () => windows.getLive2DModelState());
  ipcMain.handle(
    IPC_CHANNELS.setCurrentLive2DModel,
    (_event, modelId: string) => windows.setCurrentLive2DModel(modelId)
  );
  ipcMain.handle(
    IPC_CHANNELS.getNotificationSettings,
    () => windows.getNotificationSettings()
  );
  ipcMain.handle(
    IPC_CHANNELS.updateNotificationSettings,
    (_event, patch: Partial<Omit<NotificationSettings, "permissionState">>) =>
      windows.updateNotificationSettings(patch)
  );
  ipcMain.handle(IPC_CHANNELS.getPetSettings, () => windows.getPetSettings());
  ipcMain.handle(
    IPC_CHANNELS.updatePetSettings,
    (_event, patch: PetSettingsPatch) =>
      windows.updatePetSettings(parsePetSettingsPatch(patch))
  );
  ipcMain.handle(
    IPC_CHANNELS.setPetMousePassthrough,
    (_event, enabled: boolean) => windows.setPetMousePassthrough(enabled)
  );
  ipcMain.handle(
    IPC_CHANNELS.movePetWindowBy,
    (_event, delta: { x: number; y: number }) => windows.movePetWindowBy(delta)
  );
  ipcMain.handle(
    IPC_CHANNELS.resizePetWindowCorner,
    (
      _event,
      payload: {
        direction?: PetResizeDirection;
        corner?: PetResizeDirection;
        movement: { x: number; y: number };
      }
    ) =>
      windows.resizePetWindowCorner(
        payload.direction ?? payload.corner!,
        payload.movement
      )
  );

  ipcMain.handle(
    IPC_CHANNELS.sendChatMessage,
    async (_event, input: SendChatMessageInput) => {
      windows.recordUserActivity();
      const result = await services.chatOrchestrator.sendMessage(input, {
        onAssistantMessageCreated: (message) => {
          windows.broadcastAgentStatus("typing");
          windows.broadcastChatMessage(message);
          broadcastSentences(message);
        },
        onAssistantMessageUpdated: (message) => {
          windows.broadcastChatMessageUpdated(message);
          broadcastSentences(message);
        }
      });
      windows.broadcastAgentStatus(result.agentStatus);
      if (result.assistantMessage) {
        broadcastSentences(result.assistantMessage, true);
      }
      return result;
    }
  );

  ipcMain.handle(IPC_CHANNELS.getEventLogs, () => services.eventLogService.list());
  ipcMain.handle(IPC_CHANNELS.getMemoryDebugSnapshot, () =>
    buildLocalMemoryDebugSnapshot(services)
  );
}

function buildLocalMemoryDebugSnapshot(services: AppServices): MemoryDebugSnapshot {
  const profile = services.profileService.getOrSeedActiveProfile();
  const memory = services.memoryService.retrieveForProfile(profile.id);
  return {
    generatedAt: new Date().toISOString(),
    nodes: memory.nodes,
    edges: memory.edges,
    summaries: memory.summaries,
    recentEvents: services.eventLogService
      .list(80)
      .filter((event) => event.eventType.startsWith("memory.")),
    lastRecallTrace: services.memoryService.getLastRecallTrace()
  };
}

export interface BuildAssistantMessagePetActivitiesOptions {
  startIndex: number;
  flushRemainder?: boolean;
}

export interface BuildAssistantMessagePetActivitiesResult {
  activities: PetActivity[];
  nextIndex: number;
}

export function buildAssistantMessagePetActivities(
  message: Message,
  options: BuildAssistantMessagePetActivitiesOptions
): BuildAssistantMessagePetActivitiesResult {
  const startIndex = Math.min(Math.max(options.startIndex, 0), message.content.length);
  if (message.role !== "assistant" || !message.content.trim()) {
    return { activities: [], nextIndex: startIndex };
  }
  const result = splitTextIntoSentences(message.content, {
    startIndex,
    flushRemainder: options.flushRemainder
  });
  const createdAt = new Date().toISOString();
  return {
    activities: result.sentences.map((sentence, index) => ({
      id: `chat-reply-${message.id}-${index}-${sentence.endIndex}`,
      kind: "chat_reply",
      message: sentence.text,
      createdAt
    })),
    nextIndex: result.nextIndex
  };
}
