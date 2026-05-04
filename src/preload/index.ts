import { contextBridge, ipcRenderer } from "electron";
import { IPC_CHANNELS, type AuraBotApi } from "../shared/contracts/ipc";
import type { SendChatMessageInput } from "../shared/contracts/chat";

const api: AuraBotApi = {
  getInitialState: () => ipcRenderer.invoke(IPC_CHANNELS.getInitialState),
  openConsoleWindow: () => ipcRenderer.invoke(IPC_CHANNELS.openConsoleWindow),
  sendChatMessage: (input: SendChatMessageInput) => ipcRenderer.invoke(IPC_CHANNELS.sendChatMessage, input),
  getEventLogs: () => ipcRenderer.invoke(IPC_CHANNELS.getEventLogs)
};

contextBridge.exposeInMainWorld("aurabot", api);
