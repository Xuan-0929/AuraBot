/** Verifies desktop window placement, drag/resize bounds and platform workspace behavior.
 * The Electron double includes navigation guards while geometry remains controlled by fake screens. */
import { beforeEach, describe, expect, test, vi } from "vitest";

type Bounds = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type BrowserWindowOptions = Bounds & {
  minWidth?: number;
  minHeight?: number;
};

const electronMock = vi.hoisted(() => {
  const instances: FakeBrowserWindow[] = [];
  const setVisibleOnAllWorkspaces = vi.fn();
  const display = {
    bounds: { x: 0, y: 0, width: 1440, height: 900 },
    workArea: { x: 0, y: 0, width: 1440, height: 900 }
  };

  class FakeBrowserWindow {
    public readonly options: BrowserWindowOptions;
    private bounds: Bounds;
    public readonly webContents = {
      id: 42,
      send: vi.fn(),
      setWindowOpenHandler:vi.fn(),
      on:vi.fn(),
      session: {
        webRequest: {
          onBeforeSendHeaders: vi.fn()
        }
      }
    };

    constructor(options: BrowserWindowOptions) {
      this.options = options;
      this.bounds = {
        x: options.x,
        y: options.y,
        width: options.width,
        height: options.height
      };
      instances.push(this);
    }

    getBounds(): Bounds {
      return this.bounds;
    }

    setBounds(bounds: Bounds): void {
      this.bounds = bounds;
    }

    setPosition(x: number, y: number): void {
      this.bounds = { ...this.bounds, x, y };
    }

    isDestroyed(): boolean {
      return false;
    }

    setVisibleOnAllWorkspaces = setVisibleOnAllWorkspaces;
    on = vi.fn();
    loadURL = vi.fn();
    loadFile = vi.fn();
    show = vi.fn();
    focus = vi.fn();
    setIgnoreMouseEvents = vi.fn();
  }

  return {
    FakeBrowserWindow,
    instances,
    setVisibleOnAllWorkspaces,
    screen: {
      getPrimaryDisplay: vi.fn(() => display),
      getDisplayMatching: vi.fn(() => display),
      getCursorScreenPoint: vi.fn(() => ({ x: 0, y: 0 }))
    }
  };
});

vi.mock("electron", () => ({
  BrowserWindow: electronMock.FakeBrowserWindow,
  screen: electronMock.screen
}));

async function loadWindowModule() {
  vi.resetModules();
  electronMock.instances.length = 0;

  return import("../../../src/main/createWindow");
}

const originalPlatform = Object.getOwnPropertyDescriptor(process, "platform");

function setPlatform(platform: NodeJS.Platform): void {
  Object.defineProperty(process, "platform", {
    configurable: true,
    value: platform
  });
}

describe("pet window geometry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    electronMock.setVisibleOnAllWorkspaces.mockReset();
    if (originalPlatform) {
      Object.defineProperty(process, "platform", originalPlatform);
    }
  });

  test("creates the pet window with a renderer-safe minimum size", async () => {
    const { createPetWindow } = await loadWindowModule();

    createPetWindow();

    expect(electronMock.instances[0]?.options).toMatchObject({
      width: 300,
      height: 430,
      minWidth: 270,
      minHeight: 380
    });
  });

  test("keeps north-west corner resize from shrinking below the renderer-safe minimum", async () => {
    const { createPetWindow, resizePetWindowCorner } = await loadWindowModule();

    createPetWindow({
      savedBounds: { x: 100, y: 100, width: 300, height: 430 }
    });
    resizePetWindowCorner("nw", { x: 999, y: 999 });

    expect(electronMock.instances[0]?.getBounds()).toEqual({
      x: 130,
      y: 150,
      width: 270,
      height: 380
    });
  });

  test("resizes from a single window edge for full-border resize handles", async () => {
    const { createPetWindow, resizePetWindowCorner } = await loadWindowModule();

    createPetWindow({
      savedBounds: { x: 100, y: 100, width: 300, height: 430 }
    });
    resizePetWindowCorner("e", { x: 40, y: 999 });

    expect(electronMock.instances[0]?.getBounds()).toEqual({
      x: 100,
      y: 100,
      width: 340,
      height: 430
    });
  });

  test("normalizes legacy saved bounds that are smaller than the renderer-safe minimum", async () => {
    const { createPetWindow } = await loadWindowModule();

    createPetWindow({
      savedBounds: { x: 100, y: 100, width: 240, height: 320 }
    });

    expect(electronMock.instances[0]?.options).toMatchObject({
      x: 100,
      y: 100,
      width: 270,
      height: 380,
      minWidth: 270,
      minHeight: 380
    });
  });

  test("does not fail pet creation on Windows if visible-on-all-workspaces is unavailable", async () => {
    setPlatform("win32");
    const { createPetWindow } = await loadWindowModule();
    electronMock.setVisibleOnAllWorkspaces.mockImplementation(() => {
      throw new Error("unsupported");
    });

    expect(() => createPetWindow()).not.toThrow();
  });

  test("keeps macOS visible-on-all-workspaces failures visible", async () => {
    setPlatform("darwin");
    const { createPetWindow } = await loadWindowModule();
    electronMock.setVisibleOnAllWorkspaces.mockImplementation(() => {
      throw new Error("unsupported");
    });

    expect(() => createPetWindow()).toThrow("unsupported");
  });
});


test("applying unrelated preferences preserves interactive hit testing, while preference changes reset it", async () => {
  electronMock.setVisibleOnAllWorkspaces.mockReset();
  const windows = await loadWindowModule();
  const { DEFAULT_PET_SETTINGS } = await import("../../../src/shared/schemas/petSettings");
  const settings = { ...DEFAULT_PET_SETTINGS, clickThroughPet: true };
  windows.createPetWindow({settings});
  const native = electronMock.instances[0]!.setIgnoreMouseEvents;
  expect(native).toHaveBeenLastCalledWith(true, {forward:true});
  windows.setPetMousePassthrough(false);
  native.mockClear();
  windows.applyPetSettings({...settings, displayLanguage:'zh'});
  expect(native).not.toHaveBeenCalled();
  windows.applyPetSettings({...settings, clickThroughPet:false});
  expect(native).toHaveBeenLastCalledWith(false, {forward:true});
  windows.createPetWindow({settings});
  expect(native).toHaveBeenLastCalledWith(true, {forward:true});
});
