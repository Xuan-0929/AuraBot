import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { app, BrowserWindow, shell } from "electron";
import { createServices, createTaskRunner, type AppServices } from "../core/bootstrap/createServices";
import { MemoryFollowUpScheduler } from "../core/memory/memoryFollowUpScheduler";
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
import { updateUserTaskRunnerSettings } from "./appConfigStore";
import { createPublicAppConfig, type TaskRunnerSettingsPatch } from "../shared/contracts/settings";

let services: AppServices | undefined;
let petSettingsStore: PetSettingsStore | undefined;
let petWindowStateStore: PetWindowStateStore | undefined;
let memoryFollowUpScheduler: MemoryFollowUpScheduler | undefined;

if (process.env.AURABOT_USER_DATA_DIR) {
  app.setPath("userData", process.env.AURABOT_USER_DATA_DIR);
}

async function bootstrap(): Promise<void> {
  const userDataPath = app.getPath("userData");

  services = createServices(userDataPath);
  petSettingsStore = new PetSettingsStore(userDataPath);
  petWindowStateStore = new PetWindowStateStore(userDataPath);
  registerIpc(services, {
    openConsoleWindow,
    openSettingsWindow,
    openConfigFile: () => openUserConfigFile(userDataPath),
    updateTaskRunnerSettings: (patch) => updateTaskRunnerSettings(userDataPath, patch),
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
  memoryFollowUpScheduler = new MemoryFollowUpScheduler({
    profileService: services.profileService,
    memoryService: services.memoryService,
    eventLogService: services.eventLogService,
    voiceAdapter: services.voiceAdapter,
    broadcastPetActivity
  });
  memoryFollowUpScheduler.start();
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
  memoryFollowUpScheduler?.stop();
  services?.database.close();
});
