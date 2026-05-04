import { join } from "node:path";
import { BrowserWindow } from "electron";

let petWindow: BrowserWindow | undefined;
let consoleWindow: BrowserWindow | undefined;

export function createPetWindow(): BrowserWindow {
  if (petWindow && !petWindow.isDestroyed()) {
    return petWindow;
  }

  petWindow = new BrowserWindow({
    width: 300,
    height: 430,
    minWidth: 240,
    minHeight: 320,
    frame: false,
    transparent: true,
    resizable: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    hasShadow: false,
    title: "AuraBot Pet",
    backgroundColor: "#00000000",
    webPreferences: sharedWebPreferences()
  });
  petWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  petWindow.on("closed", () => {
    petWindow = undefined;
  });
  loadRenderer(petWindow, "pet");

  return petWindow;
}

export function openConsoleWindow(): BrowserWindow {
  if (consoleWindow && !consoleWindow.isDestroyed()) {
    consoleWindow.show();
    consoleWindow.focus();
    return consoleWindow;
  }

  consoleWindow = new BrowserWindow({
    width: 920,
    height: 700,
    minWidth: 720,
    minHeight: 540,
    title: "AuraBot Console",
    backgroundColor: "#edf2f1",
    show: false,
    webPreferences: sharedWebPreferences()
  });
  consoleWindow.on("ready-to-show", () => {
    consoleWindow?.show();
    consoleWindow?.focus();
  });
  consoleWindow.on("closed", () => {
    consoleWindow = undefined;
  });
  loadRenderer(consoleWindow, "console");

  return consoleWindow;
}

function sharedWebPreferences() {
  return {
    preload: join(__dirname, "../preload/index.js"),
    contextIsolation: true,
    nodeIntegration: false
  };
}

function loadRenderer(window: BrowserWindow, route: "pet" | "console"): void {
  const devServerUrl = process.env.ELECTRON_RENDERER_URL;

  if (devServerUrl) {
    void window.loadURL(`${devServerUrl}#${route}`);
  } else {
    void window.loadFile(join(__dirname, "../renderer/index.html"), { hash: route });
  }
}
