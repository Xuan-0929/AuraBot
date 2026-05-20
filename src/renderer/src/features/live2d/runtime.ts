import type { Application, DisplayObject } from "pixi.js";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";

export interface Live2DModelRuntime {
  autoUpdate: boolean;
  anchor: { set(x: number, y: number): void };
  scale: { set(scale: number): void };
  x: number;
  y: number;
  width: number;
  height: number;
  internalModel?: {
    width?: number;
    height?: number;
    coreModel?: {
      getParameterCount(): number;
      getParameterValueByIndex(index: number): number;
      setParameterValueByIndex(index: number, value: number): void;
    };
    motionManager?: {
      definitions?: unknown;
      update?: (model: unknown, now: number) => boolean;
      on?(event: "motionFinish", listener: () => void): void;
      off?(event: "motionFinish", listener: () => void): void;
    };
    hitAreas?: unknown;
  };
  motion(group: string, index?: number, priority?: number): unknown;
  focus(x: number, y: number, instant?: boolean): void;
  hitTest(areaName: string, x: number, y: number): boolean;
  destroy(options?: { texture?: boolean; baseTexture?: boolean }): void;
}

declare global {
  interface Window {
    PIXI?: unknown;
    Live2DCubismCore?: unknown;
  }
}

let cubismCorePromise: Promise<void> | undefined;
let live2DModelCtorPromise:
  | Promise<{
      from(modelPath: string, options?: { autoInteract?: boolean }): Promise<unknown>;
      registerTicker(ticker: unknown): void;
    }>
  | undefined;

export function ensureCubismCore(): Promise<void> {
  if (window.Live2DCubismCore) {
    return Promise.resolve();
  }

  cubismCorePromise ??= new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${cubismCoreUrl}"]`);
    if (existing) {
      if (existing.dataset.loaded === "true") {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${cubismCoreUrl}`)), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = cubismCoreUrl;
    script.async = true;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`Failed to load ${cubismCoreUrl}`));
    document.head.append(script);
  });

  return cubismCorePromise;
}

export async function ensureLive2DModelCtor() {
  live2DModelCtorPromise ??= (async () => {
    await ensureCubismCore();
    const PIXI = await import("pixi.js");
    window.PIXI = PIXI;
    const { Live2DModel } = await import("pixi-live2d-display/cubism4");
    Live2DModel.registerTicker(PIXI.Ticker);
    return Live2DModel;
  })();

  return live2DModelCtorPromise;
}

export async function loadLive2DModel(modelPath: string): Promise<Live2DModelRuntime> {
  const Live2DModel = await ensureLive2DModelCtor();
  return (await Live2DModel.from(modelPath, { autoInteract: false })) as Live2DModelRuntime;
}

export function removeAndDestroyLive2DModel(app: Application | null, model: Live2DModelRuntime | undefined): void {
  if (!model) {
    return;
  }

  try {
    model.autoUpdate = false;
  } catch {
    /* ignore */
  }

  try {
    app?.stage?.removeChild?.(model as unknown as DisplayObject);
  } catch {
    /* ignore */
  }

  try {
    model.destroy({ texture: true, baseTexture: true });
  } catch {
    /* ignore */
  }
}

export function renderLive2DFrame(app: Pick<Application, "render"> | null | undefined): void {
  try {
    app?.render();
  } catch (error) {
    console.warn("[Live2D] Pixi render failed.", error);
  }
}

export function normalizeLive2DHitAreaNames(hitAreas: unknown): string[] {
  const names = new Set<string>();
  const addName = (value: unknown) => {
    if (typeof value !== "string") {
      return;
    }
    const trimmed = value.trim();
    if (trimmed) {
      names.add(trimmed);
    }
  };

  if (Array.isArray(hitAreas)) {
    for (const item of hitAreas) {
      if (item && typeof item === "object" && "name" in item) {
        addName((item as { name?: unknown }).name);
      }
    }
    return [...names];
  }

  if (!hitAreas || typeof hitAreas !== "object") {
    return [];
  }

  for (const [key, value] of Object.entries(hitAreas as Record<string, unknown>)) {
    if (value && typeof value === "object" && "name" in value) {
      const rawName = (value as { name?: unknown }).name;
      addName(rawName);
      if (typeof rawName === "string" && rawName.trim()) {
        continue;
      }
    }
    addName(key);
  }

  return [...names];
}
