import { app, BrowserWindow } from "electron";
import { createServices, type AppServices } from "../core/bootstrap/createServices";
import { createPetWindow, openConsoleWindow } from "./createWindow";
import { registerIpc } from "./registerIpc";

let services: AppServices | undefined;

if (process.env.AURABOT_USER_DATA_DIR) {
  app.setPath("userData", process.env.AURABOT_USER_DATA_DIR);
}

async function bootstrap(): Promise<void> {
  services = createServices(app.getPath("userData"));
  registerIpc(services, { openConsoleWindow });
  createPetWindow();
}

app.whenReady().then(bootstrap);

app.on("activate", () => {
  if (services && BrowserWindow.getAllWindows().length === 0) {
    createPetWindow();
  }
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("before-quit", () => {
  services?.database.close();
});
