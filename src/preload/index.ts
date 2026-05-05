import { contextBridge, ipcRenderer } from "electron";
import {
  IPC_CHANNELS,
  type AuraBotApi,
  type TaskControlActionInput,
  type TaskResultFileActionInput
} from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput } from "../shared/contracts/chat";
import type { Message } from "../shared/schemas/message";
import type { PetActivity } from "../shared/contracts/pet";
import type { PetSettings, PetSettingsPatch } from "../shared/schemas/petSettings";
import type { Task } from "../shared/schemas/task";

const api: AuraBotApi = {
  getInitialState: () => ipcRenderer.invoke(IPC_CHANNELS.getInitialState),
  openConsoleWindow: () => ipcRenderer.invoke(IPC_CHANNELS.openConsoleWindow),
  getPetSettings: () => ipcRenderer.invoke(IPC_CHANNELS.getPetSettings),
  updatePetSettings: (patch: PetSettingsPatch) => ipcRenderer.invoke(IPC_CHANNELS.updatePetSettings, patch),
  setPetMousePassthrough: (enabled: boolean) =>
    ipcRenderer.invoke(IPC_CHANNELS.setPetMousePassthrough, enabled),
  movePetWindowBy: (delta: { x: number; y: number }) =>
    ipcRenderer.invoke(IPC_CHANNELS.movePetWindowBy, delta),
  onPetSettingsChanged: (listener: (settings: PetSettings) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, settings: PetSettings) => listener(settings);

    ipcRenderer.on(IPC_CHANNELS.petSettingsChanged, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.petSettingsChanged, handler);
  },
  onPetActivity: (listener: (activity: PetActivity) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, activity: PetActivity) => listener(activity);

    ipcRenderer.on(IPC_CHANNELS.petActivity, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.petActivity, handler);
  },
  onAgentStatusChanged: (listener: (status: AgentStatus) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, status: AgentStatus) => listener(status);

    ipcRenderer.on(IPC_CHANNELS.agentStatusChanged, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.agentStatusChanged, handler);
  },
  onChatMessageCreated: (listener: (message: Message) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, message: Message) => listener(message);

    ipcRenderer.on(IPC_CHANNELS.chatMessageCreated, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.chatMessageCreated, handler);
  },
  onTaskChanged: (listener: (task: Task) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, task: Task) => listener(task);

    ipcRenderer.on(IPC_CHANNELS.taskChanged, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.taskChanged, handler);
  },
  onTaskDeleted: (listener: (taskId: string) => void) => {
    const handler = (_event: Electron.IpcRendererEvent, taskId: string) => listener(taskId);

    ipcRenderer.on(IPC_CHANNELS.taskDeleted, handler);

    return () => ipcRenderer.removeListener(IPC_CHANNELS.taskDeleted, handler);
  },
  sendChatMessage: (input: SendChatMessageInput) => ipcRenderer.invoke(IPC_CHANNELS.sendChatMessage, input),
  cancelTask: (input: TaskControlActionInput) => ipcRenderer.invoke(IPC_CHANNELS.cancelTask, input),
  deleteTask: (input: TaskControlActionInput) => ipcRenderer.invoke(IPC_CHANNELS.deleteTask, input),
  openTaskResultFile: (input: TaskResultFileActionInput) =>
    ipcRenderer.invoke(IPC_CHANNELS.openTaskResultFile, input),
  showTaskResultFileInFolder: (input: TaskResultFileActionInput) =>
    ipcRenderer.invoke(IPC_CHANNELS.showTaskResultFileInFolder, input),
  getEventLogs: () => ipcRenderer.invoke(IPC_CHANNELS.getEventLogs),
  getMemoryDebugSnapshot: () => ipcRenderer.invoke(IPC_CHANNELS.getMemoryDebugSnapshot)
};

contextBridge.exposeInMainWorld("aurabot", api);
