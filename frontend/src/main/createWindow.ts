/**
 * 创建并管理 AuraBot 桌宠、控制台与设置窗口及其非 Task broadcasts。
 *
 * 模块负责窗口位置、尺寸、点击穿透、Live2D/聊天/通知事件投影；不再发送 Task
 * changed/deleted 事件或持有 Task DTO。
 */

import { join } from "node:path";
import { BrowserWindow, screen } from "electron";
import { IPC_CHANNELS, type PetResizeDirection } from "@shared/contracts/ipc";
import type { AgentStatus } from "@shared/contracts/chat";
import type { Live2DModelState } from "@shared/contracts/live2dModel";
import type { NotificationSettings } from "@shared/contracts/notificationSettings";
import { PET_WINDOW_DEFAULT_SIZE, PET_WINDOW_MIN_SIZE } from "@shared/contracts/petWindow";
import type { Message } from "@shared/schemas/message";
import type { PetActivity } from "@shared/contracts/pet";
import { DEFAULT_PET_SETTINGS, type PetSettings } from "@shared/schemas/petSettings";
import { createPetCursorPosition } from "./petCursorTracking";
import { clampWindowBoundsToWorkArea, placeWindowNearPet, type WindowRectangle } from "./windowPlacement";

let petWindow: BrowserWindow | undefined;
let consoleWindow: BrowserWindow | undefined;
let settingsWindow: BrowserWindow | undefined;
let currentPetSettings: PetSettings = { ...DEFAULT_PET_SETTINGS };
let onPetBoundsChanged: ((bounds: WindowRectangle) => void) | undefined;
let petCursorTrackingInterval: NodeJS.Timeout | undefined;

const CONSOLE_WINDOW_SIZE = {
  width: 1280,
  height: 820
};
const SETTINGS_WINDOW_SIZE = {
  width: 1280,
  height: 820
};
const PET_CURSOR_TRACKING_INTERVAL_MS = 50;

export interface CreatePetWindowOptions {
  settings?: PetSettings;
  live2dModelState?: Live2DModelState;
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
    minWidth: PET_WINDOW_MIN_SIZE.width,
    minHeight: PET_WINDOW_MIN_SIZE.height,
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
  setPetVisibleOnAllWorkspaces(petWindow);
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
  // For user-driven drags we intentionally clamp to the full display bounds
  // (not workArea) with no gap, so the pet can be dragged all the way to the
  // screen edge — including over the macOS dock. Using workArea + DEFAULT_GAP
  // here feels like an invisible barrier near the bottom/sides of the screen.
  const nextBounds = clampPetBoundsToDisplay({
    ...bounds,
    x: bounds.x + Math.round(delta.x),
    y: bounds.y + Math.round(delta.y)
  });

  petWindow.setPosition(nextBounds.x, nextBounds.y);
  notifyPetBoundsChanged();
}

/**
 * Resize the pet BrowserWindow from a corner drag. Uses pointer movement deltas
 * (same coordinate space as `movePetWindowBy`) so hit targets can be large CSS overlays.
 */
export function resizePetWindowCorner(direction: PetResizeDirection, movement: { x: number; y: number }): void {
  if (!petWindow || petWindow.isDestroyed()) {
    return;
  }

  const mx = Math.round(movement.x);
  const my = Math.round(movement.y);
  const b = petWindow.getBounds();
  let { x, y, width, height } = b;

  const rightEdge = x + width;
  const bottomEdge = y + height;

  if (direction.includes("e")) {
    width += mx;
    width = Math.max(PET_WINDOW_MIN_SIZE.width, width);
  }
  if (direction.includes("w")) {
    const nextWidth = Math.max(PET_WINDOW_MIN_SIZE.width, width - mx);
    width = nextWidth;
    x = rightEdge - width;
  }
  if (direction.includes("s")) {
    height += my;
    height = Math.max(PET_WINDOW_MIN_SIZE.height, height);
  }
  if (direction.includes("n")) {
    const nextHeight = Math.max(PET_WINDOW_MIN_SIZE.height, height - my);
    height = nextHeight;
    y = bottomEdge - height;
  }

  const nextBounds = clampPetBoundsToVisibleWorkArea({ x, y, width, height });
  petWindow.setBounds(nextBounds);
  notifyPetBoundsChanged();
}

export function broadcastPetActivity(activity: PetActivity): void {
  sendToRenderer(IPC_CHANNELS.petActivity, activity);
}

export function broadcastLive2DModelState(state: Live2DModelState): void {
  sendToRenderer(IPC_CHANNELS.live2dModelStateChanged, state);
}

export function broadcastNotificationSettings(settings: NotificationSettings): void {
  sendToRenderer(IPC_CHANNELS.notificationSettingsChanged, settings);
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
    width: PET_WINDOW_DEFAULT_SIZE.width,
    height: PET_WINDOW_DEFAULT_SIZE.height,
    x: 0,
    y: 0
  };
  const primaryWorkArea = screen.getPrimaryDisplay().workArea;
  const defaultBounds = {
    ...fallbackBounds,
    x: primaryWorkArea.x + primaryWorkArea.width - PET_WINDOW_DEFAULT_SIZE.width - 32,
    y: primaryWorkArea.y + primaryWorkArea.height - PET_WINDOW_DEFAULT_SIZE.height - 32
  };

  return clampPetBoundsToVisibleWorkArea(normalizePetBoundsSize(savedBounds ?? defaultBounds));
}

function clampPetBoundsToVisibleWorkArea(bounds: WindowRectangle): WindowRectangle {
  const display = screen.getDisplayMatching(bounds);

  return clampWindowBoundsToWorkArea({
    bounds,
    workArea: display.workArea
  });
}

function clampPetBoundsToDisplay(bounds: WindowRectangle): WindowRectangle {
  const display = screen.getDisplayMatching(bounds);

  return clampWindowBoundsToWorkArea({
    bounds,
    workArea: display.bounds,
    gap: 0
  });
}

function normalizePetBoundsSize(bounds: WindowRectangle): WindowRectangle {
  return {
    ...bounds,
    width: Math.max(PET_WINDOW_MIN_SIZE.width, bounds.width),
    height: Math.max(PET_WINDOW_MIN_SIZE.height, bounds.height)
  };
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

function setPetVisibleOnAllWorkspaces(window: BrowserWindow): void {
  try {
    window.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  } catch (error) {
    if (process.platform === "darwin") {
      throw error;
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
