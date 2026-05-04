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
import { EventLogService } from "../telemetry/eventLogService";
import { ProfileService } from "../profiles/profileService";

export interface AppServices {
  database: AppDatabase;
  profileService: ProfileService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  memoryService: MemoryService;
  taskService: TaskService;
  eventLogService: EventLogService;
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
  const taskRunner = new DeterministicTaskRunner(taskRepository, eventLogService);

  const services = {
    database,
    profileService,
    sessionRepository,
    messageRepository: new MessageRepository(database),
    memoryService: new MemoryService(memoryRepository),
    taskService: new TaskService(taskRepository, sessionRepository, eventLogService, taskRunner),
    eventLogService
  };

  return {
    ...services,
    chatOrchestrator: new ChatOrchestrator(services)
  };
}
