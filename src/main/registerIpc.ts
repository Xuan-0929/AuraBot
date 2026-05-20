import { randomUUID } from "node:crypto";
import { ipcMain, shell } from "electron";
import {
  IPC_CHANNELS,
  type PetResizeDirection,
  type TaskControlActionInput,
  type TaskControlActionResult,
  type TaskDetailInput,
  type TaskResultFileActionInput,
  type TaskResultFileActionResult
} from "../shared/contracts/ipc";
import type { AgentStatus, SendChatMessageInput, SendChatMessageResult } from "../shared/contracts/chat";
import type { Message } from "../shared/schemas/message";
import type { Live2DModelState } from "../shared/contracts/live2dModel";
import type { NotificationSettings } from "../shared/contracts/notificationSettings";
import type { PetActivity } from "../shared/contracts/pet";
import { parsePetSettingsPatch, type PetSettings, type PetSettingsPatch } from "../shared/schemas/petSettings";
import type { PersonaSettings } from "../shared/contracts/personaSettings";
import type { Profile } from "../shared/schemas/profile";
import type { Task } from "../shared/schemas/task";
import type { AppServices } from "../core/bootstrap/createServices";
import { shouldAutoRunTasks } from "../core/bootstrap/createServices";
import { buildTaskDetail } from "../core/tasks/taskDetails";
import { isTaskResultPathAllowed } from "../core/tasks/taskPresentation";
import { TaskExecutionCoordinator } from "./taskExecutionCoordinator";
import { splitTextIntoSentences } from "../shared/text/sentences";
import {
  createPublicAppConfig,
  type ModelConnectionTestInput,
  type ModelRoleKey,
  type ProactiveSettingsPatch,
  type PublicModelHealth,
  type PublicModelHealthMap,
  type PublicAppConfig,
  type TaskRunnerSettingsPatch
} from "../shared/contracts/settings";
import type { ModelProviderSettingsPatch } from "../shared/contracts/settings";

export interface WindowController {
  openConsoleWindow(): void;
  openSettingsWindow(): void;
  openConfigFile(): Promise<void>;
  updateTaskRunnerSettings(patch: TaskRunnerSettingsPatch): Promise<PublicAppConfig>;
  updateModelProviderSettings(patch: ModelProviderSettingsPatch): Promise<PublicAppConfig>;
  getModelHealth(): PublicModelHealthMap;
  testModelConnection(role: ModelRoleKey): Promise<PublicModelHealth>;
  updateProactiveSettings(patch: ProactiveSettingsPatch): Promise<PublicAppConfig>;
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
  resizePetWindowCorner(direction: PetResizeDirection, movement: { x: number; y: number }): void;
  broadcastPetActivity(activity: PetActivity): void;
  broadcastAgentStatus(status: AgentStatus): void;
  broadcastChatMessage(message: Message): void;
  broadcastChatMessageUpdated(message: Message): void;
  broadcastTaskChanged(task: Task): void;
  broadcastTaskDeleted(taskId: string): void;
}

export function registerIpc(services: AppServices, windows: WindowController): void {
  const taskExecutionCoordinator = new TaskExecutionCoordinator(services, windows);
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

  services.chatOrchestrator.setTaskBackgroundStarter((input) => {
    const taskId =
      input.taskId ??
      services.taskService.findTaskForChatControl({
        profileId: input.profileId,
        targetHint: input.targetHint,
        activeOnly: true
      })?.id;

    return taskId
      ? taskExecutionCoordinator.startTaskInBackground({
          profileId: input.profileId,
          taskId
        })
      : undefined;
  });

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
      modelHealth: windows.getModelHealth(),
      live2dModelState: windows.getLive2DModelState(),
      notificationSettings: windows.getNotificationSettings(),
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

  ipcMain.handle(IPC_CHANNELS.updateTaskRunnerSettings, async (_event, patch: TaskRunnerSettingsPatch) =>
    windows.updateTaskRunnerSettings(patch)
  );

  ipcMain.handle(IPC_CHANNELS.updateModelProviderSettings, async (_event, patch: ModelProviderSettingsPatch) =>
    windows.updateModelProviderSettings(patch)
  );

  ipcMain.handle(IPC_CHANNELS.getModelHealth, () => windows.getModelHealth());

  ipcMain.handle(IPC_CHANNELS.testModelConnection, async (_event, input: ModelConnectionTestInput) => {
    services.eventLogService.record("model.health.test_requested", {
      role: input.role,
      provider: services.config[input.role].provider
    });
    const health = await windows.testModelConnection(input.role);

    if (health.state === "connected") {
      services.eventLogService.record("model.health.checked", {
        role: health.role,
        provider: health.provider,
        state: health.state
      });
    } else if (health.state === "error") {
      services.eventLogService.record("model.health.failed", {
        role: health.role,
        provider: health.provider,
        state: health.state,
        lastErrorCode: health.lastErrorCode
      });
    }

    return health;
  });

  ipcMain.handle(IPC_CHANNELS.updateProactiveSettings, async (_event, patch: ProactiveSettingsPatch) =>
    windows.updateProactiveSettings(patch)
  );

  ipcMain.handle(IPC_CHANNELS.updatePersonaSettings, (_event, settings: PersonaSettings) =>
    windows.updatePersonaSettings(settings)
  );

  ipcMain.handle(IPC_CHANNELS.getLive2DModelState, () => windows.getLive2DModelState());

  ipcMain.handle(IPC_CHANNELS.setCurrentLive2DModel, (_event, modelId: string) =>
    windows.setCurrentLive2DModel(modelId)
  );

  ipcMain.handle(IPC_CHANNELS.getNotificationSettings, () => windows.getNotificationSettings());

  ipcMain.handle(
    IPC_CHANNELS.updateNotificationSettings,
    (_event, patch: Partial<Omit<NotificationSettings, "permissionState">>) =>
      windows.updateNotificationSettings(patch)
  );

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

  ipcMain.handle(
    IPC_CHANNELS.resizePetWindowCorner,
    (_event, payload: { direction?: PetResizeDirection; corner?: PetResizeDirection; movement: { x: number; y: number } }) => {
      windows.resizePetWindowCorner(payload.direction ?? payload.corner!, payload.movement);
    }
  );

  ipcMain.handle(IPC_CHANNELS.sendChatMessage, async (_event, input: SendChatMessageInput) => {
    windows.recordUserActivity();

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
    if (
      result.createdTask &&
      result.createdTask.kind === "result_output" &&
      shouldAutoRunTasks(services.config.taskRunner)
    ) {
      const startedTask = taskExecutionCoordinator.startTaskInBackground({
        profileId: result.createdTask.profileId,
        taskId: result.createdTask.id
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

  ipcMain.handle(IPC_CHANNELS.getTaskDetail, (_event, input: TaskDetailInput) => {
    const task = services.taskService.getTaskById(input.taskId);

    if (!task) {
      return undefined;
    }

    return buildTaskDetail(
      task,
      services.taskService.listTaskEvents(task.id),
      services.taskArtifactRepository.listByTask(task.id),
      services.taskInputArtifactRepository.listByTask(task.id)
    );
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
      recentEvents,
      lastRecallTrace: services.memoryService.getLastRecallTrace()
    };
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
  const artifacts = services.taskArtifactRepository.listByTask(task.id);

  return isTaskResultPathAllowed(events, input.path, artifacts) ? input.path : undefined;
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
        }
      }),
      createdAt: new Date().toISOString()
    };
  }

  return undefined;
}
