<template>
  <div />
</template>

<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import type { Application, DisplayObject } from "pixi.js";
import { Ticker } from "pixi.js";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";
import { useMotionQueueStore } from "../stores/motionQueue";

/** Scale PIXI ticker delta for Live2D only (motion, physics, blink). Values below 1 slow playback. */
const LIVE2D_MOTION_TIME_SCALE = 0.62;

/** Ignore sub-pixel focus churn from IPC to avoid parameter fighting with motion. */
const FOCUS_EPSILON_PX = 1.25;

interface FocusPoint {
  x: number;
  y: number;
}

interface PointerPoint {
  x: number;
  y: number;
}

interface Live2DModelRuntime {
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
  update(dt: number): void;
  destroy(options?: { texture?: boolean; baseTexture?: boolean }): void;
}

const props = defineProps<{
  app: Application | null;
  modelPath: string;
  focusAt: FocusPoint;
  canvasSize: { width: number; height: number };
  pointerAt?: PointerPoint;
}>();

const emit = defineEmits<{
  interactiveChange: [boolean];
  modelReady: [];
}>();

const motionQueueStore = useMotionQueueStore();
let model: Live2DModelRuntime | undefined;
let disposed = false;
let lastMotionSignature: string | undefined;
let loadGeneration = 0;
let live2dTickHandler: (() => void) | undefined;
let lastCanvasW = 0;
let lastCanvasH = 0;
let lastFocusX: number | undefined;
let lastFocusY: number | undefined;
let Live2DModelCtor: {
  from(modelPath: string, options?: { autoInteract?: boolean }): Promise<unknown>;
  registerTicker(ticker: unknown): void;
} | null = null;

function detachLive2dTicker() {
  if (live2dTickHandler) {
    Ticker.shared.remove(live2dTickHandler);
    live2dTickHandler = undefined;
  }
}

function attachLive2dTicker(nextModel: Live2DModelRuntime) {
  detachLive2dTicker();
  nextModel.autoUpdate = false;
  live2dTickHandler = () => {
    nextModel.update(Ticker.shared.deltaMS * LIVE2D_MOTION_TIME_SCALE);
  };
  Ticker.shared.add(live2dTickHandler);
}

function teardownModelFromStage(app: Application | null) {
  detachLive2dTicker();
  if (model && app) {
    app.stage.removeChild(model as unknown as DisplayObject);
    try {
      model.destroy({ texture: true, baseTexture: true });
    } catch {
      // Best-effort teardown when the GL context is already gone.
    }
  }
  model = undefined;
  motionQueueStore.clearQueue();
}

watch(
  () => [props.app, props.modelPath] as const,
  async ([app, modelPath]) => {
    const gen = ++loadGeneration;

    if (!app || !modelPath) {
      teardownModelFromStage(app);
      lastMotionSignature = undefined;
      return;
    }

    teardownModelFromStage(app);
    lastMotionSignature = undefined;

    await ensureScript(cubismCoreUrl);
    if (gen !== loadGeneration || disposed) {
      return;
    }

    const Live2DModel = await ensureLive2DModelCtor();
    if (gen !== loadGeneration || disposed) {
      return;
    }

    const nextModel = (await Live2DModel.from(modelPath, { autoInteract: false })) as Live2DModelRuntime;
    if (gen !== loadGeneration || disposed) {
      try {
        nextModel.destroy({ texture: true, baseTexture: true });
      } catch {
        /* ignore */
      }
      return;
    }

    model = nextModel;
    model.anchor.set(0.5, 0.95);
    attachLive2dTicker(model);
    app.stage.addChild(model as unknown as DisplayObject);
    motionQueueStore.setMotionDefinitions(model.internalModel?.motionManager?.definitions);
    resizeModel();
    emit("modelReady");
  },
  { immediate: true }
);

watch(
  () => props.canvasSize,
  (size) => {
    const w = Math.max(1, Math.round(size.width));
    const h = Math.max(1, Math.round(size.height));
    if (w === lastCanvasW && h === lastCanvasH) {
      return;
    }
    lastCanvasW = w;
    lastCanvasH = h;
    resizeModel();
  },
  { deep: true }
);

watch(
  () => props.focusAt,
  (focusAt) => {
    if (!model) {
      return;
    }
    if (
      lastFocusX !== undefined &&
      lastFocusY !== undefined &&
      Math.abs(focusAt.x - lastFocusX) < FOCUS_EPSILON_PX &&
      Math.abs(focusAt.y - lastFocusY) < FOCUS_EPSILON_PX
    ) {
      return;
    }
    lastFocusX = focusAt.x;
    lastFocusY = focusAt.y;
    model.focus(focusAt.x, focusAt.y);
  },
  { deep: true }
);

watch(
  () => motionQueueStore.currentMotion,
  async (motion) => {
    if (!model || !motion) {
      return;
    }
    const signature = `${motion.group}:${motion.index ?? "default"}`;
    if (lastMotionSignature === signature) {
      motionQueueStore.completeCurrentMotion();
      return;
    }

    lastMotionSignature = signature;
    try {
      const result = model.motion(motion.group, motion.index);
      if (result && typeof (result as Promise<unknown>).then === "function") {
        await result;
      }
    } finally {
      motionQueueStore.completeCurrentMotion();
    }
  }
);

watch(
  () => props.pointerAt,
  (pointer) => {
    if (!model || !pointer) {
      emit("interactiveChange", false);
      return;
    }
    const areaNames = model.internalModel?.hitAreas?.map((item) => item.name).filter(Boolean) as string[] | undefined;
    if (!areaNames || areaNames.length === 0) {
      emit("interactiveChange", false);
      return;
    }
    const isInteractive = areaNames.some((name) => model?.hitTest(name, pointer.x, pointer.y));
    emit("interactiveChange", isInteractive);
  },
  { deep: true }
);

function resizeModel() {
  if (!model) {
    return;
  }
  const width = Math.max(1, props.canvasSize.width);
  const height = Math.max(1, props.canvasSize.height);
  const modelWidth = model.internalModel?.width ?? model.width ?? width;
  const modelHeight = model.internalModel?.height ?? model.height ?? height;
  const scale = Math.min(width / modelWidth, height / modelHeight) * 0.92;
  model.scale.set(scale);
  model.x = width / 2;
  model.y = height;
}

async function ensureScript(src: string): Promise<void> {
  if ((window as Window & { Live2DCubismCore?: unknown }).Live2DCubismCore) {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`Failed to load ${src}`)), { once: true });
      return;
    }
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.append(script);
  });
}

async function ensureLive2DModelCtor() {
  if (Live2DModelCtor) {
    return Live2DModelCtor;
  }

  const PIXI = await import("pixi.js");
  (window as Window & { PIXI?: unknown }).PIXI = PIXI;
  const { Live2DModel } = await import("pixi-live2d-display/cubism4");
  Live2DModel.registerTicker(PIXI.Ticker);
  Live2DModelCtor = Live2DModel;

  return Live2DModelCtor;
}

onUnmounted(() => {
  disposed = true;
  loadGeneration += 1;
  teardownModelFromStage(props.app);
});
</script>
