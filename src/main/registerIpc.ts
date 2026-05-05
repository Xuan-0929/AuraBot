import { randomUUID } from "node:crypto";
import { ipcMain, shell } from "electron";
import {
  IPC_CHANNELS,
  type TaskControlActionInput,
  type TaskControlActionResult,
  type TaskResultFileActionInput,
  type TaskResultFileActionResult
} from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput, SendChatMessageResult } from "../shared/contracts/chat";
import type { Message } from "../shared/schemas/message";
import type { PetActivity } from "../shared/contracts/pet";
import { parsePetSettingsPatch, type PetSettings, type PetSettingsPatch } from "../shared/schemas/petSettings";
import type { Task } from "../shared/schemas/task";
import type { AppServices } from "../core/bootstrap/createServices";
import { buildTaskSettledMessageDraft, isTaskResultPathAllowed } from "../core/tasks/taskPresentation";

export interface WindowController {
  openConsoleWindow(): void;
  getPetSettings(): PetSettings;
  updatePetSettings(patch: PetSettingsPatch): PetSettings;
  setPetMousePassthrough(enabled: boolean): void;
  movePetWindowBy(delta: { x: number; y: number }): void;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastAgentStatus(status: AgentStatus): void;
  broadcastChatMessage(message: Message): void;
  broadcastChatMessageUpdated(message: Message): void;
  broadcastTaskChanged(task: Task): void;
  broadcastTaskDeleted(taskId: string): void;
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
    const result = await services.chatOrchestrator.sendMessage(input, {
      onAssistantMessageCreated: (message) => {
        if (!message) {
          return;
        }

        windows.broadcastAgentStatus("typing");
        windows.broadcastChatMessage(message);
      },
      onAssistantMessageUpdated: (message) => {
        if (message) {
          windows.broadcastChatMessageUpdated(message);
        }
      }
    });
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

  ipcMain.handle(IPC_CHANNELS.openTaskResultFile, async (_event, input: TaskResultFileActionInput) =>
    openTaskResultFile(services, input)
  );

  ipcMain.handle(IPC_CHANNELS.cancelTask, (_event, input: TaskControlActionInput) => {
    const profile = services.profileService.getOrSeedActiveProfile();
    const task = services.taskService.cancelTaskFromConsole({
      profileId: profile.id,
      taskId: input.taskId
    });

    if (!task) {
      return {
        ok: false,
        error: "这个任务现在不能取消。"
      } satisfies TaskControlActionResult;
    }

    windows.broadcastTaskChanged(task);
    windows.broadcastAgentStatus("idle");

    return {
      ok: true,
      task
    } satisfies TaskControlActionResult;
  });

  ipcMain.handle(IPC_CHANNELS.deleteTask, (_event, input: TaskControlActionInput) => {
    const profile = services.profileService.getOrSeedActiveProfile();
    const deleted = services.taskService.deleteSettledTaskFromConsole({
      profileId: profile.id,
      taskId: input.taskId
    });

    if (!deleted) {
      return {
        ok: false,
        error: "这个任务还不能从列表里移除。"
      } satisfies TaskControlActionResult;
    }

    windows.broadcastTaskDeleted(input.taskId);

    return {
      ok: true,
      taskId: input.taskId
    } satisfies TaskControlActionResult;
  });

  ipcMain.handle(IPC_CHANNELS.showTaskResultFileInFolder, (_event, input: TaskResultFileActionInput) =>
    showTaskResultFileInFolder(services, input)
  );

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
      recentEvents,
      lastRecallTrace: services.memoryService.getLastRecallTrace()
    };
  });
}

function createTaskSettledMessage(services: AppServices, task: Task): Message {
  const profile = services.profileService.getOrSeedActiveProfile();
  const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
  const events = services.taskService.listTaskEvents(task.id);
  const draft = buildTaskSettledMessageDraft(task, events);

  return services.messageRepository.create({
    sessionId: mainSession.id,
    profileId: profile.id,
    role: "assistant",
    content: draft.content,
    attachments: draft.attachments
  });
}

async function openTaskResultFile(
  services: AppServices,
  input: TaskResultFileActionInput
): Promise<TaskResultFileActionResult> {
  const path = resolveTaskResultPath(services, input);

  if (!path) {
    return {
      ok: false,
      error: "这个结果文件没有通过任务记录校验。"
    };
  }

  const error = await shell.openPath(path);

  return error ? { ok: false, error } : { ok: true };
}

function showTaskResultFileInFolder(
  services: AppServices,
  input: TaskResultFileActionInput
): TaskResultFileActionResult {
  const path = resolveTaskResultPath(services, input);

  if (!path) {
    return {
      ok: false,
      error: "这个结果文件没有通过任务记录校验。"
    };
  }

  shell.showItemInFolder(path);

  return { ok: true };
}

function resolveTaskResultPath(services: AppServices, input: TaskResultFileActionInput): string | undefined {
  const task = services.taskService.getTaskById(input.taskId);

  if (!task) {
    return undefined;
  }

  const events = services.taskService.listTaskEvents(task.id);

  return isTaskResultPathAllowed(events, input.path) ? input.path : undefined;
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
