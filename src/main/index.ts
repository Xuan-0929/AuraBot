import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { app, BrowserWindow, shell } from "electron";
import { createServices, createTaskRunner, type AppServices } from "../core/bootstrap/createServices";
import { MemoryService } from "../core/memory/memoryService";
import { ProactiveTriggerEngine } from "../core/proactive/proactiveTriggerEngine";
import { createChatModel } from "../core/llm/chatModel";
import { createGateModel } from "../core/llm/gateModel";
import { createModelHealthService, type ModelHealthService } from "../core/llm/modelHealth";
import { createVoiceAdapter } from "../core/voice/voiceAdapter";
import {
  createMemoryExtractor,
  createMemoryMergePlanner,
  createMemoryRecallPlanner,
  createMemoryRelationPlanner
} from "../core/memory/modelBackedMemoryModel";
import {
  applyPetSettings,
  broadcastAgentStatus,
  broadcastChatMessage,
  broadcastChatMessageUpdated,
  broadcastPetActivity,
  broadcastTaskChanged,
  broadcastTaskDeleted,
  createPetWindow,
  movePetWindowBy,
  openConsoleWindow,
  openSettingsWindow,
  setPetMousePassthrough
} from "./createWindow";
import { PetSettingsStore } from "./petSettingsStore";
import { PetWindowStateStore } from "./petWindowStateStore";
import { registerIpc } from "./registerIpc";
import {
  updateUserModelProviderSettings,
  updateUserProactiveSettings,
  updateUserTaskRunnerSettings
} from "./appConfigStore";
import {
  createPublicAppConfig,
  type ModelRoleKey,
  type ModelProviderSettingsPatch,
  type ProactiveSettingsPatch,
  type PublicModelHealthMap,
  type TaskRunnerSettingsPatch
} from "../shared/contracts/settings";

let services: AppServices | undefined;
let petSettingsStore: PetSettingsStore | undefined;
let petWindowStateStore: PetWindowStateStore | undefined;
let proactiveTriggerEngine: ProactiveTriggerEngine | undefined;
let modelHealthService: ModelHealthService | undefined;
let modelHealthSnapshot: PublicModelHealthMap | undefined;

if (process.env.AURABOT_USER_DATA_DIR) {
  app.setPath("userData", process.env.AURABOT_USER_DATA_DIR);
}

async function bootstrap(): Promise<void> {
  const userDataPath = app.getPath("userData");

  services = createServices(userDataPath);
  modelHealthService = createModelHealthService();
  modelHealthSnapshot = modelHealthService.inspectAll(services.config);
  petSettingsStore = new PetSettingsStore(userDataPath);
  petWindowStateStore = new PetWindowStateStore(userDataPath);
  registerIpc(services, {
    openConsoleWindow,
    openSettingsWindow,
    openConfigFile: () => openUserConfigFile(userDataPath),
    updateTaskRunnerSettings: (patch) => updateTaskRunnerSettings(userDataPath, patch),
    updateModelProviderSettings: (patch) => updateModelProviderSettings(userDataPath, patch),
    getModelHealth: () => getModelHealth(),
    testModelConnection: (role) => testModelConnection(role),
    updateProactiveSettings: (patch) => updateProactiveSettings(userDataPath, patch),
    recordUserActivity: () => proactiveTriggerEngine?.recordUserActivity(),
    getPetSettings: () => petSettingsStore?.get() ?? {
      clickThroughPet: false,
      displayLanguage: "en"
    },
    updatePetSettings: (patch) => {
      const settings = petSettingsStore?.update(patch) ?? {
        clickThroughPet: false,
        displayLanguage: "en"
      };

      applyPetSettings(settings);

      return settings;
    },
    setPetMousePassthrough,
    movePetWindowBy,
    broadcastPetActivity,
    broadcastAgentStatus,
    broadcastChatMessage,
    broadcastChatMessageUpdated,
    broadcastTaskChanged,
    broadcastTaskDeleted
  });
  createPetWindow({
    settings: petSettingsStore.get(),
    savedBounds: petWindowStateStore.getBounds(),
    onBoundsChanged: (bounds) => petWindowStateStore?.saveBounds(bounds)
  });
  proactiveTriggerEngine = new ProactiveTriggerEngine({
    get profileService() {
      return services!.profileService;
    },
    get memoryService() {
      return services!.memoryService;
    },
    get taskService() {
      return services!.taskService;
    },
    get eventLogService() {
      return services!.eventLogService;
    },
    get voiceAdapter() {
      return services!.voiceAdapter;
    },
    broadcastPetActivity,
    broadcastTaskChanged
  }, services.config.proactive);
  proactiveTriggerEngine.start();
}

async function openUserConfigFile(userDataPath: string): Promise<void> {
  const configPath = join(userDataPath, "config.json");

  if (!existsSync(configPath)) {
    mkdirSync(userDataPath, { recursive: true });
    writeFileSync(configPath, "{}\n", "utf8");
  }

  const error = await shell.openPath(configPath);

  if (error) {
    shell.showItemInFolder(configPath);
  }
}

async function updateTaskRunnerSettings(userDataPath: string, patch: TaskRunnerSettingsPatch) {
  if (!services) {
    throw new Error("AuraBot services are not ready.");
  }

  const nextConfig = updateUserTaskRunnerSettings(userDataPath, services.config, patch);
  services.config.taskRunner = nextConfig.taskRunner;
  services.taskService.setTaskRunner(
    createTaskRunner(nextConfig.taskRunner, {
      userDataPath,
      taskRepository: services.taskRepository,
      eventLogService: services.eventLogService
    })
  );

  return createPublicAppConfig(services.config);
}

async function updateModelProviderSettings(userDataPath: string, patch: ModelProviderSettingsPatch) {
  if (!services) {
    throw new Error("AuraBot services are not ready.");
  }

  const nextConfig = updateUserModelProviderSettings(userDataPath, services.config, patch);
  services.config[patch.role] = nextConfig[patch.role];

  if (patch.role === "gateModel") {
    services.gateModel = createGateModel(nextConfig.gateModel);
    services.chatOrchestrator.updateModelDependencies({ gateModel: services.gateModel });
  }

  if (patch.role === "chatModel") {
    services.chatModel = createChatModel(nextConfig.chatModel);
    services.voiceAdapter = createVoiceAdapter(nextConfig.chatModel);
    services.chatOrchestrator.updateModelDependencies({
      chatModel: services.chatModel,
      voiceAdapter: services.voiceAdapter
    });
  }

  if (patch.role === "memoryModel") {
    services.memoryExtractor = createMemoryExtractor(nextConfig.memoryModel);
    services.memoryService = new MemoryService(services.memoryService.getRepository(), {
      mergePlanner: createMemoryMergePlanner(nextConfig.memoryModel),
      relationPlanner: createMemoryRelationPlanner(nextConfig.memoryModel),
      recallPlanner: createMemoryRecallPlanner(nextConfig.memoryModel)
    });
    services.chatOrchestrator.updateModelDependencies({
      memoryExtractor: services.memoryExtractor,
      memoryService: services.memoryService
    });
  }

  modelHealthSnapshot = {
    ...getModelHealth(),
    [patch.role]: modelHealthService!.inspectRole(patch.role, nextConfig[patch.role])
  };

  return createPublicAppConfig(services.config);
}

async function updateProactiveSettings(userDataPath: string, patch: ProactiveSettingsPatch) {
  if (!services) {
    throw new Error("AuraBot services are not ready.");
  }

  const nextConfig = updateUserProactiveSettings(userDataPath, services.config, patch);
  services.config.proactive = nextConfig.proactive;
  proactiveTriggerEngine?.updateOptions(nextConfig.proactive);

  return createPublicAppConfig(services.config);
}

app.whenReady().then(bootstrap);

app.on("activate", () => {
  if (services && BrowserWindow.getAllWindows().length === 0) {
    createPetWindow({
      settings: petSettingsStore?.get(),
      savedBounds: petWindowStateStore?.getBounds(),
      onBoundsChanged: (bounds) => petWindowStateStore?.saveBounds(bounds)
    });
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  proactiveTriggerEngine?.stop();
  services?.database.close();
});

function getModelHealth(): PublicModelHealthMap {
  if (!services || !modelHealthService) {
    throw new Error("AuraBot services are not ready.");
  }

  modelHealthSnapshot ??= modelHealthService.inspectAll(services.config);

  return modelHealthSnapshot;
}

async function testModelConnection(role: ModelRoleKey) {
  if (!services || !modelHealthService) {
    throw new Error("AuraBot services are not ready.");
  }

  const health = await modelHealthService.testRoleConnection(role, services.config[role]);
  modelHealthSnapshot = {
    ...getModelHealth(),
    [role]: health
  };

  return health;
}
