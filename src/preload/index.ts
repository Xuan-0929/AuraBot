import { contextBridge, ipcRenderer } from "electron";
import { IPC_CHANNELS, type AuraBotApi } from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput } from "../shared/contracts/chat";
import type { PetActivity } from "../shared/contracts/pet";
import type { PetSettings, PetSettingsPatch } from "../shared/schemas/petSettings";

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
  sendChatMessage: (input: SendChatMessageInput) => ipcRenderer.invoke(IPC_CHANNELS.sendChatMessage, input),
  getEventLogs: () => ipcRenderer.invoke(IPC_CHANNELS.getEventLogs)
};

contextBridge.exposeInMainWorld("aurabot", api);
