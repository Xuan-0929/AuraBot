<template>
  <div class="live2d-vue-model-anchor" aria-hidden="true" />
</template>

<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import type { Application, DisplayObject } from "pixi.js";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";
import { calculateLive2DPlacement } from "./live2dPlacement";
import { useMotionQueueStore } from "../stores/motionQueue";

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
let lastCanvasW = 0;
let lastCanvasH = 0;
let Live2DModelCtor: {
  from(modelPath: string, options?: { autoInteract?: boolean }): Promise<unknown>;
  registerTicker(ticker: unknown): void;
} | null = null;

function teardownModelFromStage(app: Application | null) {
  if (model && app) {
    // Detach from the shared ticker before destroy so pixi-live2d-display does
    // not keep ticking a freed model on subsequent frames.
    try {
      model.autoUpdate = false;
    } catch {
      /* ignore */
    }
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
    // pixi-live2d-display defaults autoUpdate to true after init; reasserting
    // here documents the intent and re-arms the shared ticker if a previous
    // teardown turned it off via this same model instance.
    model.autoUpdate = true;
    app.stage.addChild(model as unknown as DisplayObject);
    motionQueueStore.setMotionDefinitions(model.internalModel?.motionManager?.definitions);
    resizeModel();
    // Prime focus so the head/eyes look at the most recent cursor position
    // even before the next IPC tick (otherwise the model stares straight ahead
    // until the cursor moves again).
    model.focus(props.focusAt.x, props.focusAt.y);
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
    // Forward every IPC sample to the focus controller so it can interpolate
    // smoothly toward the cursor. The controller already smooths on its own,
    // so an additional jitter threshold here prevents slow drags from being
    // tracked at all.
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
      const awaitCompletion = motion.group !== "Idle";
      if (awaitCompletion && result && typeof (result as Promise<unknown>).then === "function") {
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
  const hostSize = {
    width: Math.max(1, Math.round(props.canvasSize.width)),
    height: Math.max(1, Math.round(props.canvasSize.height))
  };
  const modelSize = {
    width: model.internalModel?.width ?? model.width ?? hostSize.width,
    height: model.internalModel?.height ?? model.height ?? hostSize.height
  };
  const placement = calculateLive2DPlacement({
    hostSize,
    modelSize,
    compact: false
  });
  model.scale.set(placement.scale);
  model.x = placement.x;
  model.y = placement.y;
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
