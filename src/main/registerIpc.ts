import { ipcMain } from "electron";
import { IPC_CHANNELS } from "../shared/contracts/ipc";
import type { SendChatMessageInput } from "../shared/contracts/chat";
import type { AppServices } from "../core/bootstrap/createServices";

export interface WindowController {
  openConsoleWindow(): void;
}

export function registerIpc(services: AppServices, windows: WindowController): void {
  ipcMain.handle(IPC_CHANNELS.getInitialState, () => {
    const profile = services.profileService.getOrSeedActiveProfile();
    const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);

    services.eventLogService.record("app.initial_state_loaded", {
      profileId: profile.id,
      sessionId: mainSession.id
    });

    return {
      profile,
      mainSessionId: mainSession.id,
      messages: services.messageRepository.listBySession(mainSession.id),
      tasks: services.taskService.listTasksForProfile(profile.id),
      agentStatus: "idle"
    };
  });

  ipcMain.handle(IPC_CHANNELS.openConsoleWindow, () => {
    windows.openConsoleWindow();
  });

  ipcMain.handle(IPC_CHANNELS.sendChatMessage, (_event, input: SendChatMessageInput) =>
    services.chatOrchestrator.sendMessage(input)
  );

  ipcMain.handle(IPC_CHANNELS.getEventLogs, () => services.eventLogService.list());
}
