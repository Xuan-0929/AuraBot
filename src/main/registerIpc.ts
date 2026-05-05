import { randomUUID } from "node:crypto";
import { ipcMain } from "electron";
import { IPC_CHANNELS } from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput, SendChatMessageResult } from "../shared/contracts/chat";
import type { Message } from "../shared/schemas/message";
import type { PetActivity } from "../shared/contracts/pet";
import { parsePetSettingsPatch, type PetSettings, type PetSettingsPatch } from "../shared/schemas/petSettings";
import type { Task } from "../shared/schemas/task";
import type { AppServices } from "../core/bootstrap/createServices";

export interface WindowController {
  openConsoleWindow(): void;
  getPetSettings(): PetSettings;
  updatePetSettings(patch: PetSettingsPatch): PetSettings;
  setPetMousePassthrough(enabled: boolean): void;
  movePetWindowBy(delta: { x: number; y: number }): void;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastAgentStatus(status: AgentStatus): void;
  broadcastChatMessage(message: Message): void;
  broadcastTaskChanged(task: Task): void;
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
    if (result.createdTask && services.config.taskRunner.provider === "codex") {
      const startedTask = services.taskService.runTaskInBackground({
        profileId: result.createdTask.profileId,
        taskId: result.createdTask.id,
        onProgress: (progressTask) => {
          windows.broadcastTaskChanged(progressTask);
        },
        onSettled: (settledTask) => {
          windows.broadcastTaskChanged(settledTask);
          windows.broadcastAgentStatus("idle");
          const assistantMessage = createTaskSettledMessage(services, settledTask);
          windows.broadcastChatMessage(assistantMessage);
          const activity = buildPetActivityFromTask(settledTask);

          if (activity) {
            windows.broadcastPetActivity(activity);
          }
        }
      });

      if (startedTask) {
        result.createdTask = startedTask;
        result.agentStatus = "tasking";
      }
    }
    const activity = buildPetActivity(input, result);

    windows.broadcastAgentStatus(result.agentStatus);

    if (activity) {
      windows.broadcastPetActivity(activity);
    }

    return result;
  });

  ipcMain.handle(IPC_CHANNELS.getEventLogs, () => services.eventLogService.list());

  ipcMain.handle(IPC_CHANNELS.getMemoryDebugSnapshot, () => {
    const profile = services.profileService.getOrSeedActiveProfile();
    const memoryContext = services.memoryService.retrieveForProfile(profile.id);
    const recentEvents = services.eventLogService
      .list(80)
      .filter((event) => event.eventType.startsWith("memory."));

    return {
      generatedAt: new Date().toISOString(),
      nodes: memoryContext.nodes,
      edges: memoryContext.edges,
      summaries: memoryContext.summaries,
      recentEvents
    };
  });
}

function createTaskSettledMessage(services: AppServices, task: Task): Message {
  const profile = services.profileService.getOrSeedActiveProfile();
  const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);

  return services.messageRepository.create({
    sessionId: mainSession.id,
    profileId: profile.id,
    role: "assistant",
    content:
      task.status === "completed"
        ? `这个任务完成了：${task.latestProgress}`
        : `这个任务${task.status === "failed" ? "失败了" : `现在是 ${task.status}`}：${task.latestProgress}`
  });
}

function buildPetActivityFromTask(task: Task): PetActivity | undefined {
  if (task.status !== "completed" && task.status !== "failed") {
    return undefined;
  }

  return {
    id: randomUUID(),
    kind: "task_completed",
    message: task.status === "completed" ? `任务完成：${task.title}` : `任务失败：${task.title}`,
    createdAt: new Date().toISOString()
  };
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
