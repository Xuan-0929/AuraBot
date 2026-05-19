import { loadConfig } from "./loadConfig";
import { MemoryService } from "../memory/memoryService";
import { ChatOrchestrator } from "../orchestration/chatOrchestrator";
import { createAppDatabase, type AppDatabase } from "../storage/db";
import { MemoryRepository } from "../storage/repositories/memoryRepository";
import { MessageRepository } from "../storage/repositories/messageRepository";
import { ProfileRepository } from "../storage/repositories/profileRepository";
import { SessionRepository } from "../storage/repositories/sessionRepository";
import { TaskRepository } from "../storage/repositories/taskRepository";
import { TaskArtifactRepository } from "../storage/repositories/taskArtifactRepository";
import { TaskInputArtifactRepository } from "../storage/repositories/taskInputArtifactRepository";
import { TaskService } from "../tasks/taskService";
import { TaskInputAttachmentService } from "../tasks/taskInputAttachments";
import { DeterministicTaskRunner } from "../tasks/taskRunner";
import { CliAgentClientImpl, MultiCliTaskRunner } from "../tasks/cliTaskRunner";
import { EventLogService } from "../telemetry/eventLogService";
import { ProfileService } from "../profiles/profileService";
import { PersonaSettingsService } from "../profiles/personaSettingsService";
import { createChatModel, type ChatModel } from "../llm/chatModel";
import { createGateModel, type GateModel } from "../llm/gateModel";
import { createVoiceAdapter, type VoiceAdapter } from "../voice/voiceAdapter";
import type { MemoryExtractor } from "../memory/memoryExtractor";
import {
  createMemoryExtractor,
  createMemoryMergePlanner,
  createMemoryRecallPlanner,
  createMemoryRelationPlanner
} from "../memory/modelBackedMemoryModel";
import type { AppConfig, TaskRunnerConfig } from "../../shared/schemas/config";
import type { TaskRunner } from "../tasks/taskRunner";
import { join } from "node:path";
import { detectTaskCliCommand } from "../tasks/taskCliCommandDiscovery";

export interface AppServices {
  config: AppConfig;
  database: AppDatabase;
  profileService: ProfileService;
  personaSettingsService: PersonaSettingsService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  taskRepository: TaskRepository;
  taskArtifactRepository: TaskArtifactRepository;
  taskInputArtifactRepository: TaskInputArtifactRepository;
  taskInputAttachmentService: TaskInputAttachmentService;
  memoryExtractor: MemoryExtractor;
  memoryService: MemoryService;
  taskService: TaskService;
  eventLogService: EventLogService;
  gateModel: GateModel;
  chatModel: ChatModel;
  voiceAdapter: VoiceAdapter;
  chatOrchestrator: ChatOrchestrator;
}

export function createServices(userDataPath: string): AppServices {
  const config = loadConfig(userDataPath);
  const database = createAppDatabase({ filePath: config.storagePath });
  const profileRepository = new ProfileRepository(database);
  const profileService = new ProfileService(profileRepository);
  const personaSettingsService = new PersonaSettingsService(profileService, profileRepository);
  const memoryRepository = new MemoryRepository(database);
  const sessionRepository = new SessionRepository(database);
  const eventLogService = new EventLogService(database);
  const taskRepository = new TaskRepository(database);
  const taskArtifactRepository = new TaskArtifactRepository(database);
  const taskInputArtifactRepository = new TaskInputArtifactRepository(database);
  const taskInputAttachmentService = new TaskInputAttachmentService(
    taskInputArtifactRepository,
    taskRepository,
    join(userDataPath, "task-inputs")
  );
  const taskRunner = createTaskRunner(config.taskRunner, {
    userDataPath,
    taskRepository,
    taskInputArtifactRepository,
    eventLogService
  });
  const gateModel = createGateModel(config.gateModel);
  const chatModel = createChatModel(config.chatModel);
  const voiceAdapter = createVoiceAdapter(config.chatModel);
  const memoryExtractor = createMemoryExtractor(config.memoryModel);
  const memoryMergePlanner = createMemoryMergePlanner(config.memoryModel);
  const memoryRelationPlanner = createMemoryRelationPlanner(config.memoryModel);
  const memoryRecallPlanner = createMemoryRecallPlanner(config.memoryModel);

  const services = {
    config,
    database,
    profileService,
    personaSettingsService,
    sessionRepository,
    taskRepository,
    taskArtifactRepository,
    taskInputArtifactRepository,
    taskInputAttachmentService,
    messageRepository: new MessageRepository(database),
    memoryExtractor,
    memoryService: new MemoryService(memoryRepository, {
      mergePlanner: memoryMergePlanner,
      relationPlanner: memoryRelationPlanner,
      recallPlanner: memoryRecallPlanner
    }),
    taskService: new TaskService(taskRepository, sessionRepository, eventLogService, taskRunner),
    eventLogService,
    gateModel,
    chatModel,
    voiceAdapter
  };
  services.taskService.recoverOrphanedActiveTasks();

  return {
    ...services,
    chatOrchestrator: new ChatOrchestrator(services)
  };
}

export function createTaskRunner(
  config: TaskRunnerConfig,
  dependencies: {
    userDataPath: string;
    taskRepository: TaskRepository;
    taskInputArtifactRepository: TaskInputArtifactRepository;
    eventLogService: EventLogService;
  }
): TaskRunner {
  if (!shouldAutoRunTasks(config)) {
    return new DeterministicTaskRunner(dependencies.taskRepository, dependencies.eventLogService);
  }

  return new MultiCliTaskRunner(dependencies.taskRepository, dependencies.eventLogService, new CliAgentClientImpl(), {
    runners: config.runners.map((runner) => ({
      ...runner,
      command: runner.command ?? getDefaultTaskCliCommand(runner.id),
      workingDirectory: runner.workingDirectory ?? config.workingDirectory ?? process.cwd()
    })),
    outputDirectory: join(dependencies.userDataPath, "task-agent-output"),
    inputArtifactRepository: dependencies.taskInputArtifactRepository
  });
}

export function shouldAutoRunTasks(config: TaskRunnerConfig): boolean {
  return config.runners.some((runner) => runner.enabled) || config.provider === "codex";
}

function getDefaultTaskCliCommand(id: "codex" | "claude" | "hermes"): string {
  return detectTaskCliCommand(id) ?? id;
}
