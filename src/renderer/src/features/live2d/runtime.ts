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
    motionManager?: { definitions?: unknown };
    hitAreas?: Array<{ name?: string }>;
  };
  motion(group: string, index?: number): unknown;
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
  if (!model || !app) {
    return;
  }

  try {
    model.autoUpdate = false;
  } catch {
    /* ignore */
  }

  app.stage.removeChild(model as unknown as DisplayObject);

  try {
    model.destroy({ texture: true, baseTexture: true });
  } catch {
    /* ignore */
  }
}
