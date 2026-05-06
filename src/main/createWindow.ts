import { join } from "node:path";
import { BrowserWindow, screen } from "electron";
import { IPC_CHANNELS } from "../shared/contracts/ipc";
import type { AgentStatus } from "../shared/contracts/chat";
import type { Message } from "../shared/schemas/message";
import type { PetActivity } from "../shared/contracts/pet";
import { DEFAULT_PET_SETTINGS, type PetSettings } from "../shared/schemas/petSettings";
import type { Task } from "../shared/schemas/task";
import { createPetCursorPosition } from "./petCursorTracking";
import { clampWindowBoundsToWorkArea, placeWindowNearPet, type WindowRectangle } from "./windowPlacement";

let petWindow: BrowserWindow | undefined;
let consoleWindow: BrowserWindow | undefined;
let settingsWindow: BrowserWindow | undefined;
let currentPetSettings: PetSettings = { ...DEFAULT_PET_SETTINGS };
let onPetBoundsChanged: ((bounds: WindowRectangle) => void) | undefined;
let petCursorTrackingInterval: NodeJS.Timeout | undefined;

const PET_WINDOW_SIZE = {
  width: 300,
  height: 430
};

const CONSOLE_WINDOW_SIZE = {
  width: 920,
  height: 700
};
const SETTINGS_WINDOW_SIZE = {
  width: 760,
  height: 720
};
const PET_CURSOR_TRACKING_INTERVAL_MS = 50;

export interface CreatePetWindowOptions {
  settings?: PetSettings;
  savedBounds?: WindowRectangle;
  onBoundsChanged?: (bounds: WindowRectangle) => void;
}

export function createPetWindow(options: CreatePetWindowOptions = {}): BrowserWindow {
  const settings = options.settings ?? currentPetSettings;

  currentPetSettings = settings;
  onPetBoundsChanged = options.onBoundsChanged ?? onPetBoundsChanged;

  if (petWindow && !petWindow.isDestroyed()) {
    applyPetSettings(settings);
    return petWindow;
  }

  const initialBounds = getInitialPetBounds(options.savedBounds);

  petWindow = new BrowserWindow({
    x: initialBounds.x,
    y: initialBounds.y,
    width: initialBounds.width,
    height: initialBounds.height,
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
    stopPetCursorTracking();
  });
  loadRenderer(petWindow, "pet");
  applyPetSettings(settings);
  startPetCursorTracking();

  return petWindow;
}

export function openConsoleWindow(): BrowserWindow {
  if (consoleWindow && !consoleWindow.isDestroyed()) {
    consoleWindow.show();
    consoleWindow.focus();
    return consoleWindow;
  }

  consoleWindow = new BrowserWindow({
    width: CONSOLE_WINDOW_SIZE.width,
    height: CONSOLE_WINDOW_SIZE.height,
    minWidth: 720,
    minHeight: 540,
    title: "AuraBot Console",
    backgroundColor: "#edf2f1",
    show: false,
    webPreferences: sharedWebPreferences()
  });
  positionConsoleNearPet(consoleWindow);
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

export function openSettingsWindow(): BrowserWindow {
  if (settingsWindow && !settingsWindow.isDestroyed()) {
    settingsWindow.show();
    settingsWindow.focus();
    return settingsWindow;
  }

  settingsWindow = new BrowserWindow({
    width: SETTINGS_WINDOW_SIZE.width,
    height: SETTINGS_WINDOW_SIZE.height,
    minWidth: 620,
    minHeight: 560,
    title: "AuraBot Settings",
    backgroundColor: "#edf2f1",
    show: false,
    webPreferences: sharedWebPreferences()
  });
  positionSettingsNearPet(settingsWindow);
  settingsWindow.on("ready-to-show", () => {
    settingsWindow?.show();
    settingsWindow?.focus();
  });
  settingsWindow.on("closed", () => {
    settingsWindow = undefined;
  });
  loadRenderer(settingsWindow, "settings");

  return settingsWindow;
}

export function applyPetSettings(settings: PetSettings): void {
  currentPetSettings = settings;
  setPetMousePassthrough(settings.clickThroughPet);
  broadcastPetSettings(settings);
}

export function setPetMousePassthrough(enabled: boolean): void {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }

  petWindow.setIgnoreMouseEvents(enabled, { forward: true });
}

export function movePetWindowBy(delta: { x: number; y: number }): void {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }

  const bounds = petWindow.getBounds();
  const nextBounds = clampPetBoundsToVisibleWorkArea({
    ...bounds,
    x: bounds.x + Math.round(delta.x),
    y: bounds.y + Math.round(delta.y)
  });

  petWindow.setPosition(nextBounds.x, nextBounds.y);
  notifyPetBoundsChanged();
}

export function broadcastPetActivity(activity: PetActivity): void {
  sendToRenderer(IPC_CHANNELS.petActivity, activity);
}

export function broadcastAgentStatus(status: AgentStatus): void {
  sendToRenderer(IPC_CHANNELS.agentStatusChanged, status);
}

export function broadcastChatMessage(message: Message): void {
  sendToRenderer(IPC_CHANNELS.chatMessageCreated, message);
}

export function broadcastChatMessageUpdated(message: Message): void {
  sendToRenderer(IPC_CHANNELS.chatMessageUpdated, message);
}

export function broadcastTaskChanged(task: Task): void {
  sendToRenderer(IPC_CHANNELS.taskChanged, task);
}

export function broadcastTaskDeleted(taskId: string): void {
  sendToRenderer(IPC_CHANNELS.taskDeleted, taskId);
}

export function broadcastPetSettings(settings: PetSettings): void {
  sendToRenderer(IPC_CHANNELS.petSettingsChanged, settings);
}

function sharedWebPreferences() {
  return {
    preload: join(__dirname, "../preload/index.js"),
    contextIsolation: true,
    nodeIntegration: false
  };
}

function loadRenderer(window: BrowserWindow, route: "pet" | "console" | "settings"): void {
  const devServerUrl = process.env.ELECTRON_RENDERER_URL;

  if (devServerUrl) {
    void window.loadURL(`${devServerUrl}#${route}`);
  } else {
    void window.loadFile(join(__dirname, "../renderer/index.html"), { hash: route });
  }
}

function getInitialPetBounds(savedBounds?: WindowRectangle): WindowRectangle {
  const fallbackBounds = {
    width: PET_WINDOW_SIZE.width,
    height: PET_WINDOW_SIZE.height,
    x: 0,
    y: 0
  };
  const primaryWorkArea = screen.getPrimaryDisplay().workArea;
  const defaultBounds = {
    ...fallbackBounds,
    x: primaryWorkArea.x + primaryWorkArea.width - PET_WINDOW_SIZE.width - 32,
    y: primaryWorkArea.y + primaryWorkArea.height - PET_WINDOW_SIZE.height - 32
  };

  return clampPetBoundsToVisibleWorkArea(savedBounds ?? defaultBounds);
}

function clampPetBoundsToVisibleWorkArea(bounds: WindowRectangle): WindowRectangle {
  const display = screen.getDisplayMatching(bounds);

  return clampWindowBoundsToWorkArea({
    bounds,
    workArea: display.workArea
  });
}

function notifyPetBoundsChanged(): void {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }

  onPetBoundsChanged?.(petWindow.getBounds());
}

function positionConsoleNearPet(window: BrowserWindow): void {
  const targetSize = window.getBounds();
  const petBounds = petWindow && !petWindow.isDestroyed() ? petWindow.getBounds() : undefined;
  const display = petBounds ? screen.getDisplayMatching(petBounds) : screen.getPrimaryDisplay();

  if (!petBounds) {
    const x = display.workArea.x + display.workArea.width - targetSize.width - 12;
    const y = display.workArea.y + display.workArea.height - targetSize.height - 12;
    window.setPosition(x, y);
    return;
  }

  const position = placeWindowNearPet({
    petBounds,
    targetSize,
    workArea: display.workArea
  });
  window.setPosition(position.x, position.y);
}

function positionSettingsNearPet(window: BrowserWindow): void {
  const targetSize = window.getBounds();
  const petBounds = petWindow && !petWindow.isDestroyed() ? petWindow.getBounds() : undefined;
  const display = petBounds ? screen.getDisplayMatching(petBounds) : screen.getPrimaryDisplay();
  const position = petBounds
    ? placeWindowNearPet({
        petBounds,
        targetSize,
        workArea: display.workArea
      })
    : {
        x: display.workArea.x + display.workArea.width - targetSize.width - 28,
        y: display.workArea.y + display.workArea.height - targetSize.height - 28
      };

  window.setPosition(position.x, position.y);
}

function sendToRenderer(channel: string, payload: unknown): void {
  for (const window of [petWindow, consoleWindow, settingsWindow]) {
    if (window && !window.isDestroyed()) {
      window.webContents.send(channel, payload);
    }
  }
}

function startPetCursorTracking(): void {
  if (petCursorTrackingInterval) {
    return;
  }

  petCursorTrackingInterval = setInterval(() => {
    if (!petWindow || petWindow.isDestroyed()) {
      stopPetCursorTracking();
      return;
    }

    const cursor = screen.getCursorScreenPoint();
    const petBounds = petWindow.getBounds();

    petWindow.webContents.send(
      IPC_CHANNELS.petCursorPosition,
      createPetCursorPosition({
        cursor,
        petBounds
      })
    );
  }, PET_CURSOR_TRACKING_INTERVAL_MS);
  petCursorTrackingInterval.unref?.();
}

function stopPetCursorTracking(): void {
  if (!petCursorTrackingInterval) {
    return;
  }

  clearInterval(petCursorTrackingInterval);
  petCursorTrackingInterval = undefined;
}
