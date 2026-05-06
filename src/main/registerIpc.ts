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
import { buildTaskSettledMessageDraftWithVoice, isTaskResultPathAllowed } from "../core/tasks/taskPresentation";
import { splitTextIntoSentences } from "../shared/text/sentences";
import { createPublicAppConfig } from "../shared/contracts/settings";

export interface WindowController {
  openConsoleWindow(): void;
  openSettingsWindow(): void;
  openConfigFile(): Promise<void>;
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
  const assistantPetActivityCursors = new Map<string, number>();
  const broadcastAssistantMessagePetActivities = (message: Message, flushRemainder = false) => {
    const result = buildAssistantMessagePetActivities(message, {
      startIndex: assistantPetActivityCursors.get(message.id) ?? 0,
      flushRemainder
    });

    assistantPetActivityCursors.set(message.id, result.nextIndex);

    for (const activity of result.activities) {
      windows.broadcastPetActivity(activity);
    }
  };

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
      appConfig: createPublicAppConfig(services.config),
      debugPetOverlay: process.env.AURABOT_DEBUG_PET === "1"
    };
  });

  ipcMain.handle(IPC_CHANNELS.openConsoleWindow, () => {
    windows.openConsoleWindow();
  });

  ipcMain.handle(IPC_CHANNELS.openSettingsWindow, () => {
    windows.openSettingsWindow();
  });

  ipcMain.handle(IPC_CHANNELS.openConfigFile, () => windows.openConfigFile());

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
        broadcastAssistantMessagePetActivities(message);
      },
      onAssistantMessageUpdated: (message) => {
        if (message) {
          windows.broadcastChatMessageUpdated(message);
          broadcastAssistantMessagePetActivities(message);
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
        onSettled: async (settledTask) => {
          windows.broadcastTaskChanged(settledTask);
          windows.broadcastAgentStatus("idle");
          const assistantMessage = await createTaskSettledMessage(services, settledTask);
          windows.broadcastChatMessage(assistantMessage);
          const activity = await buildPetActivityFromTask(services, settledTask);

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
    const activity = await buildPetActivity(services, input, result);

    windows.broadcastAgentStatus(result.agentStatus);

    if (result.assistantMessage) {
      broadcastAssistantMessagePetActivities(result.assistantMessage, true);
    }

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

async function createTaskSettledMessage(services: AppServices, task: Task): Promise<Message> {
  const profile = services.profileService.getOrSeedActiveProfile();
  const mainSession = services.sessionRepository.getOrCreateMainSession(profile.id);
  const events = services.taskService.listTaskEvents(task.id);
  const draft = await buildTaskSettledMessageDraftWithVoice(task, events, {
    soul: profile.soul,
    voiceAdapter: services.voiceAdapter
  });

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

async function buildPetActivityFromTask(services: AppServices, task: Task): Promise<PetActivity | undefined> {
  if (task.status !== "completed" && task.status !== "failed") {
    return undefined;
  }

  const profile = services.profileService.getOrSeedActiveProfile();

  return {
    id: randomUUID(),
    kind: "task_completed",
    message: await services.voiceAdapter.render({
      scene: "pet.task_activity",
      channel: "pet_notice",
      soul: profile.soul,
      facts: {
        taskTitle: task.title,
        status: task.status,
        resultSubject: task.title
      },
      fallbackText: task.status === "completed" ? `${task.title}弄好了，要不要看一眼？` : `${task.title}没跑通，我在这儿等你看。`
    }),
    createdAt: new Date().toISOString()
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
  const content = message.content;
  const startIndex = Math.min(Math.max(options.startIndex, 0), content.length);

  if (message.role !== "assistant" || !content.trim()) {
    return {
      activities: [],
      nextIndex: startIndex
    };
  }

  const activities: PetActivity[] = [];
  const createdAt = new Date().toISOString();
  const result = splitTextIntoSentences(content, {
    startIndex,
    flushRemainder: options.flushRemainder
  });

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

export async function buildPetActivity(
  services: AppServices,
  _input: SendChatMessageInput,
  result: SendChatMessageResult
): Promise<PetActivity | undefined> {
  if (result.updatedTask?.status === "completed") {
    const profile = services.profileService.getOrSeedActiveProfile();

    return {
      id: randomUUID(),
      kind: "task_completed",
      message: await services.voiceAdapter.render({
        scene: "pet.task_activity",
        channel: "pet_notice",
        soul: profile.soul,
        facts: {
          taskTitle: result.updatedTask.title,
          status: result.updatedTask.status,
          resultSubject: result.updatedTask.title
        },
        fallbackText: `${result.updatedTask.title}弄好了，要不要看一眼？`
      }),
      createdAt: new Date().toISOString()
    };
  }

  return undefined;
}
