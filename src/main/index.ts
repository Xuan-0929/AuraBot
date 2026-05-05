import { app, BrowserWindow } from "electron";
import { createServices, type AppServices } from "../core/bootstrap/createServices";
import { MemoryFollowUpScheduler } from "../core/memory/memoryFollowUpScheduler";
import {
  applyPetSettings,
  broadcastAgentStatus,
  broadcastChatMessage,
  broadcastPetActivity,
  broadcastTaskChanged,
  broadcastTaskDeleted,
  createPetWindow,
  movePetWindowBy,
  openConsoleWindow,
  setPetMousePassthrough
} from "./createWindow";
import { PetSettingsStore } from "./petSettingsStore";
import { PetWindowStateStore } from "./petWindowStateStore";
import { registerIpc } from "./registerIpc";

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
    getPetSettings: () => petSettingsStore?.get() ?? {
      clickThroughPet: false
    },
    updatePetSettings: (patch) => {
      const settings = petSettingsStore?.update(patch) ?? {
        clickThroughPet: false
      };

      applyPetSettings(settings);

      return settings;
    },
    setPetMousePassthrough,
    movePetWindowBy,
    broadcastPetActivity,
    broadcastAgentStatus,
    broadcastChatMessage,
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
    broadcastPetActivity
  });
  memoryFollowUpScheduler.start();
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
