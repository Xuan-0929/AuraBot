/**
 * 通过 contextBridge 向 Renderer 暴露最小、类型化的非 Task Electron API。
 *
 * 每个方法只转发保留的共享 IPC channel；不暴露本地文件路径解析，也不再注册
 * Task controls、Task events、debug 或结果文件动作。
 */

import { contextBridge, ipcRenderer } from "electron";
import type { AgentStatus, SendChatMessageInput } from "@shared/contracts/chat";
import type { Live2DModelState } from "@shared/contracts/live2dModel";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import type { PetActivity } from "@shared/contracts/pet";
import {
  IPC_CHANNELS,
  type AuraBotApi,
  type PetResizeDirection
} from "@shared/contracts/ipc";
import type { Message } from "@shared/schemas/message";
import type { PetSettings, PetSettingsPatch } from "@shared/schemas/petSettings";

function subscribe<T>(channel: string, listener: (value: T) => void): () => void {
  const handler = (_event: Electron.IpcRendererEvent, value: T) => listener(value);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

const api: AuraBotApi = {
  getInitialState: () => ipcRenderer.invoke(IPC_CHANNELS.getInitialState),
  openConsoleWindow: () => ipcRenderer.invoke(IPC_CHANNELS.openConsoleWindow),
  openSettingsWindow: () => ipcRenderer.invoke(IPC_CHANNELS.openSettingsWindow),
  openConfigFile: () => ipcRenderer.invoke(IPC_CHANNELS.openConfigFile),
  updateModelProviderSettings: (patch) =>
    ipcRenderer.invoke(IPC_CHANNELS.updateModelProviderSettings, patch),
  getModelHealth: () => ipcRenderer.invoke(IPC_CHANNELS.getModelHealth),
  testModelConnection: (input) => ipcRenderer.invoke(IPC_CHANNELS.testModelConnection, input),
  updateProactiveSettings: (patch) =>
    ipcRenderer.invoke(IPC_CHANNELS.updateProactiveSettings, patch),
  getBackendStatus: () => ipcRenderer.invoke(IPC_CHANNELS.getBackendStatus),
  updatePersonaSettings: (settings) =>
    ipcRenderer.invoke(IPC_CHANNELS.updatePersonaSettings, settings),
  getLive2DModelState: () => ipcRenderer.invoke(IPC_CHANNELS.getLive2DModelState),
  setCurrentLive2DModel: (modelId) =>
    ipcRenderer.invoke(IPC_CHANNELS.setCurrentLive2DModel, modelId),
  getNotificationSettings: () => ipcRenderer.invoke(IPC_CHANNELS.getNotificationSettings),
  updateNotificationSettings: (patch) =>
    ipcRenderer.invoke(IPC_CHANNELS.updateNotificationSettings, patch),
  getPetSettings: () => ipcRenderer.invoke(IPC_CHANNELS.getPetSettings),
  updatePetSettings: (patch: PetSettingsPatch) =>
    ipcRenderer.invoke(IPC_CHANNELS.updatePetSettings, patch),
  setPetMousePassthrough: (enabled) =>
    ipcRenderer.invoke(IPC_CHANNELS.setPetMousePassthrough, enabled),
  movePetWindowBy: (delta) => ipcRenderer.invoke(IPC_CHANNELS.movePetWindowBy, delta),
  resizePetWindowCorner: (
    direction: PetResizeDirection,
    movement: { x: number; y: number }
  ) => ipcRenderer.invoke(IPC_CHANNELS.resizePetWindowCorner, { direction, movement }),
  onPetSettingsChanged: (listener: (settings: PetSettings) => void) =>
    subscribe(IPC_CHANNELS.petSettingsChanged, listener),
  onPetActivity: (listener: (activity: PetActivity) => void) =>
    subscribe(IPC_CHANNELS.petActivity, listener),
  onLive2DModelStateChanged: (listener: (state: Live2DModelState) => void) =>
    subscribe(IPC_CHANNELS.live2dModelStateChanged, listener),
  onNotificationSettingsChanged: (listener: (settings: NotificationSettings) => void) =>
    subscribe(IPC_CHANNELS.notificationSettingsChanged, listener),
  onPetCursorPosition: (listener) => subscribe(IPC_CHANNELS.petCursorPosition, listener),
  onAgentStatusChanged: (listener: (status: AgentStatus) => void) =>
    subscribe(IPC_CHANNELS.agentStatusChanged, listener),
  onChatMessageCreated: (listener: (message: Message) => void) =>
    subscribe(IPC_CHANNELS.chatMessageCreated, listener),
  onChatMessageUpdated: (listener: (message: Message) => void) =>
    subscribe(IPC_CHANNELS.chatMessageUpdated, listener),
  sendChatMessage: (input: SendChatMessageInput) =>
    ipcRenderer.invoke(IPC_CHANNELS.sendChatMessage, input),
  getEventLogs: () => ipcRenderer.invoke(IPC_CHANNELS.getEventLogs),
  getMemoryDebugSnapshot: () => ipcRenderer.invoke(IPC_CHANNELS.getMemoryDebugSnapshot)
};

contextBridge.exposeInMainWorld("aurabot", api);
