import { randomUUID } from "node:crypto";
import { ipcMain } from "electron";
import { IPC_CHANNELS } from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput, SendChatMessageResult } from "../shared/contracts/chat";
import type { PetActivity } from "../shared/contracts/pet";
import { parsePetSettingsPatch, type PetSettings, type PetSettingsPatch } from "../shared/schemas/petSettings";
import type { AppServices } from "../core/bootstrap/createServices";

export interface WindowController {
  openConsoleWindow(): void;
  getPetSettings(): PetSettings;
  updatePetSettings(patch: PetSettingsPatch): PetSettings;
  setPetMousePassthrough(enabled: boolean): void;
  movePetWindowBy(delta: { x: number; y: number }): void;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastAgentStatus(status: AgentStatus): void;
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
      agentStatus: "idle",
      petSettings: windows.getPetSettings(),
      debugPetOverlay: process.env.AURABOT_DEBUG_PET === "1"
    };
  });

  ipcMain.handle(IPC_CHANNELS.openConsoleWindow, () => {
    windows.openConsoleWindow();
  });

  ipcMain.handle(IPC_CHANNELS.getPetSettings, () => windows.getPetSettings());

  ipcMain.handle(IPC_CHANNELS.updatePetSettings, (_event, patch: PetSettingsPatch) =>
    windows.updatePetSettings(parsePetSettingsPatch(patch))
  );

  ipcMain.handle(IPC_CHANNELS.setPetMousePassthrough, (_event, enabled: boolean) => {
    windows.setPetMousePassthrough(enabled);
  });

  ipcMain.handle(IPC_CHANNELS.movePetWindowBy, (_event, delta: { x: number; y: number }) => {
    windows.movePetWindowBy(delta);
  });

  ipcMain.handle(IPC_CHANNELS.sendChatMessage, async (_event, input: SendChatMessageInput) => {
    const result = await services.chatOrchestrator.sendMessage(input);
    const activity = buildPetActivity(input, result);

    windows.broadcastAgentStatus(result.agentStatus);

    if (activity) {
      windows.broadcastPetActivity(activity);
    }

    return result;
  });

  ipcMain.handle(IPC_CHANNELS.getEventLogs, () => services.eventLogService.list());
}

function buildPetActivity(input: SendChatMessageInput, result: SendChatMessageResult): PetActivity | undefined {
  if (result.updatedTask?.status === "completed") {
    return {
      id: randomUUID(),
      kind: "task_completed",
      message: `任务完成：${result.updatedTask.title}`,
      createdAt: new Date().toISOString()
    };
  }

  if (input.source === "pet_voice" && result.assistantMessage) {
    return {
      id: randomUUID(),
      kind: "voice_reply",
      message: result.assistantMessage.content,
      createdAt: new Date().toISOString()
    };
  }

  return undefined;
}
