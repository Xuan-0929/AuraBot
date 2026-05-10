import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { app, BrowserWindow, Notification, shell } from "electron";
import { createServices, createTaskRunner, type AppServices } from "../core/bootstrap/createServices";
import { createLive2DModelRegistry, type Live2DModelRegistry } from "../core/live2d/modelRegistry";
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
  broadcastLive2DModelState,
  broadcastNotificationSettings,
  broadcastPetActivity,
  broadcastTaskChanged,
  broadcastTaskDeleted,
  createPetWindow,
  movePetWindowBy,
  resizePetWindowCorner,
  openConsoleWindow,
  openSettingsWindow,
  setPetMousePassthrough
} from "./createWindow";
import { PetSettingsStore } from "./petSettingsStore";
import { Live2DModelStore } from "./live2dModelStore";
import { NotificationSettingsStore } from "./notificationSettingsStore";
import { PetWindowStateStore } from "./petWindowStateStore";
import { SystemNotificationService } from "./systemNotificationService";
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
import type { PersonaSettings } from "../shared/contracts/personaSettings";
import type { Live2DModelState } from "../shared/contracts/live2dModel";
import type { NotificationSettings } from "../shared/contracts/notificationSettings";
import type { Profile } from "../shared/schemas/profile";

let services: AppServices | undefined;
let petSettingsStore: PetSettingsStore | undefined;
let live2dModelStore: Live2DModelStore | undefined;
let notificationSettingsStore: NotificationSettingsStore | undefined;
let petWindowStateStore: PetWindowStateStore | undefined;
let proactiveTriggerEngine: ProactiveTriggerEngine | undefined;
let modelHealthService: ModelHealthService | undefined;
let modelHealthSnapshot: PublicModelHealthMap | undefined;
let live2dModelRegistry: Live2DModelRegistry | undefined;
let systemNotificationService: SystemNotificationService | undefined;

if (process.env.AURABOT_USER_DATA_DIR) {
  app.setPath("userData", process.env.AURABOT_USER_DATA_DIR);
}

async function bootstrap(): Promise<void> {
  const userDataPath = app.getPath("userData");

  services = createServices(userDataPath);
  modelHealthService = createModelHealthService();
  modelHealthSnapshot = modelHealthService.inspectAll(services.config);
  petSettingsStore = new PetSettingsStore(userDataPath);
  live2dModelStore = new Live2DModelStore(userDataPath);
  notificationSettingsStore = new NotificationSettingsStore(userDataPath);
  systemNotificationService = new SystemNotificationService({
    isSupported: () => Notification.isSupported(),
    createNotification: (payload) => new Notification(payload),
    openConsoleWindow: () => {
      openConsoleWindow();
    },
    getDisplayLanguage: () => petSettingsStore?.get().displayLanguage ?? "en"
  });
  live2dModelRegistry = createLive2DModelRegistry({
    modelRootPath: resolveLive2DModelRootPath()
  });
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
    updatePersonaSettings: (settings) => updatePersonaSettings(settings),
    getLive2DModelState: () => getLive2DModelState(),
    setCurrentLive2DModel: (modelId) => setCurrentLive2DModel(modelId),
    getNotificationSettings: () => getNotificationSettings(),
    updateNotificationSettings: (patch) => updateNotificationSettings(patch),
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
    resizePetWindowCorner,
    broadcastPetActivity,
    broadcastAgentStatus,
    broadcastChatMessage,
    broadcastChatMessageUpdated,
    broadcastTaskChanged,
    broadcastTaskDeleted
  });
  createPetWindow({
    settings: petSettingsStore.get(),
    live2dModelState: getLive2DModelState(),
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
    broadcastTaskChanged,
    deliverSystemNotification: ({ kind, message }) => {
      const settings = getNotificationSettings();

      if (
        kind === "memory_follow_up" ||
        kind === "task_reminder" ||
        kind === "proactive_idle"
      ) {
        systemNotificationService?.deliver({
          kind,
          message,
          settings
        });
      }
    }
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

function updatePersonaSettings(settings: PersonaSettings): Profile {
  if (!services) {
    throw new Error("AuraBot services are not ready.");
  }

  return services.personaSettingsService.updateActivePersonaSettings(settings);
}

function getLive2DModelState(): Live2DModelState {
  if (!live2dModelRegistry || !live2dModelStore) {
    throw new Error("AuraBot Live2D model services are not ready.");
  }

  const currentModel = live2dModelRegistry.resolveCurrentModel(live2dModelStore.get().currentModelId);
  const models = live2dModelRegistry.listAvailableModels();

  return {
    currentModelId: currentModel?.id ?? "",
    currentModelPath: currentModel?.manifestPath ?? "",
    models
  };
}

function setCurrentLive2DModel(modelId: string): Live2DModelState {
  if (!live2dModelStore) {
    throw new Error("AuraBot Live2D model services are not ready.");
  }

  live2dModelStore.update({ currentModelId: modelId });
  const state = getLive2DModelState();
  broadcastLive2DModelState(state);

  return state;
}

function getNotificationSettings(): NotificationSettings {
  if (!notificationSettingsStore) {
    throw new Error("AuraBot notification settings are not ready.");
  }

  return notificationSettingsStore.get(resolveNotificationPermissionState());
}

function updateNotificationSettings(
  patch: Partial<Omit<NotificationSettings, "permissionState">>
): NotificationSettings {
  if (!notificationSettingsStore) {
    throw new Error("AuraBot notification settings are not ready.");
  }

  const settings = notificationSettingsStore.update(patch, resolveNotificationPermissionState());
  broadcastNotificationSettings(settings);

  return settings;
}

function resolveNotificationPermissionState(): NotificationSettings["permissionState"] {
  return Notification.isSupported() ? "system_managed" : "unsupported";
}

function resolveLive2DModelRootPath(): string {
  return app.isPackaged
    ? join(__dirname, "../renderer/assets/live2d")
    : join(process.cwd(), "src/renderer/public/assets/live2d");
}

app.whenReady().then(bootstrap);

app.on("activate", () => {
  if (services && BrowserWindow.getAllWindows().length === 0) {
    createPetWindow({
      settings: petSettingsStore?.get(),
      live2dModelState: getLive2DModelState(),
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
