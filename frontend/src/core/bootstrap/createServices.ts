/**
 * 组合 Electron 当前保留的 Chat、Memory、Profile、模型与 SQLite 服务。
 *
 * 启动时先以 userData context 执行数据库迁移，再构造非 Task service graph；本模块
 * 不创建 Task repositories、runner、orphan recovery 或子进程。
 */

import type { AppConfig } from "@shared/schemas/config";
import { createChatModel, type ChatModel } from "../llm/chatModel";
import { createGateModel, type GateModel } from "../llm/gateModel";
import type { MemoryExtractor } from "../memory/memoryExtractor";
import {
  createMemoryExtractor,
  createMemoryMergePlanner,
  createMemoryRecallPlanner,
  createMemoryRelationPlanner
} from "../memory/modelBackedMemoryModel";
import { MemoryService } from "../memory/memoryService";
import { ChatOrchestrator } from "../orchestration/chatOrchestrator";
import { PersonaSettingsService } from "../profiles/personaSettingsService";
import { ProfileService } from "../profiles/profileService";
import { createAppDatabase, type AppDatabase } from "../storage/db";
import { MemoryRepository } from "../storage/repositories/memoryRepository";
import { MessageRepository } from "../storage/repositories/messageRepository";
import { ProfileRepository } from "../storage/repositories/profileRepository";
import { SessionRepository } from "../storage/repositories/sessionRepository";
import { EventLogService } from "../telemetry/eventLogService";
import { createVoiceAdapter, type VoiceAdapter } from "../voice/voiceAdapter";
import { loadConfig } from "./loadConfig";

export interface AppServices {
  config: AppConfig;
  database: AppDatabase;
  profileService: ProfileService;
  personaSettingsService: PersonaSettingsService;
  sessionRepository: SessionRepository;
  messageRepository: MessageRepository;
  memoryExtractor: MemoryExtractor;
  memoryService: MemoryService;
  eventLogService: EventLogService;
  gateModel: GateModel;
  chatModel: ChatModel;
  voiceAdapter: VoiceAdapter;
  chatOrchestrator: ChatOrchestrator;
}

export function createServices(userDataPath: string): AppServices {
  const config = loadConfig(userDataPath);
  const database = createAppDatabase({
    filePath: config.storagePath,
    userDataPath
  });
  const profileRepository = new ProfileRepository(database);
  const profileService = new ProfileService(profileRepository);
  const personaSettingsService = new PersonaSettingsService(profileService, profileRepository);
  const sessionRepository = new SessionRepository(database);
  const messageRepository = new MessageRepository(database);
  const eventLogService = new EventLogService(database);
  const gateModel = createGateModel(config.gateModel);
  const chatModel = createChatModel(config.chatModel);
  const voiceAdapter = createVoiceAdapter(config.chatModel);
  const memoryExtractor = createMemoryExtractor(config.memoryModel);
  const memoryService = new MemoryService(new MemoryRepository(database), {
    mergePlanner: createMemoryMergePlanner(config.memoryModel),
    relationPlanner: createMemoryRelationPlanner(config.memoryModel),
    recallPlanner: createMemoryRecallPlanner(config.memoryModel)
  });

  const services = {
    config,
    database,
    profileService,
    personaSettingsService,
    sessionRepository,
    messageRepository,
    memoryExtractor,
    memoryService,
    eventLogService,
    gateModel,
    chatModel,
    voiceAdapter
  };

  return {
    ...services,
    chatOrchestrator: new ChatOrchestrator(services)
  };
}
