import { loadConfig } from "./loadConfig";
import { MemoryService } from "../memory/memoryService";
import { ChatOrchestrator } from "../orchestration/chatOrchestrator";
import { createAppDatabase, type AppDatabase } from "../storage/db";
import { MemoryRepository } from "../storage/repositories/memoryRepository";
import { MessageRepository } from "../storage/repositories/messageRepository";
import { ProfileRepository } from "../storage/repositories/profileRepository";
import { SessionRepository } from "../storage/repositories/sessionRepository";
import { TaskRepository } from "../storage/repositories/taskRepository";
import { TaskService } from "../tasks/taskService";
import { DeterministicTaskRunner } from "../tasks/taskRunner";
import { CliCodexAgentClient, CodexTaskRunner } from "../tasks/codexTaskRunner";
import { EventLogService } from "../telemetry/eventLogService";
import { ProfileService } from "../profiles/profileService";
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
import { existsSync } from "node:fs";
import { join } from "node:path";

export interface AppServices {
  config: AppConfig;
  database: AppDatabase;
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
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
  const memoryRepository = new MemoryRepository(database);
  const sessionRepository = new SessionRepository(database);
  const eventLogService = new EventLogService(database);
  const taskRepository = new TaskRepository(database);
  const taskRunner = createTaskRunner(config.taskRunner, {
    userDataPath,
    taskRepository,
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
    sessionRepository,
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

function createTaskRunner(
  config: TaskRunnerConfig,
  dependencies: {
    userDataPath: string;
    taskRepository: TaskRepository;
    eventLogService: EventLogService;
  }
): TaskRunner {
  if (config.provider !== "codex") {
    return new DeterministicTaskRunner(dependencies.taskRepository, dependencies.eventLogService);
  }

  return new CodexTaskRunner(dependencies.taskRepository, dependencies.eventLogService, new CliCodexAgentClient(), {
    command: config.codexCommand ?? getDefaultCodexCommand(),
    workingDirectory: config.workingDirectory ?? process.cwd(),
    model: config.model,
    sandbox: config.sandbox,
    approvalPolicy: config.approvalPolicy,
    timeoutMs: config.timeoutMs,
    outputDirectory: join(dependencies.userDataPath, "task-agent-output")
  });
}

function getDefaultCodexCommand(): string {
  const appBundledCodex = "/Applications/Codex.app/Contents/Resources/codex";

  return existsSync(appBundledCodex) ? appBundledCodex : "codex";
}
