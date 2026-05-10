<template>
  <div />
</template>

<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import { Ticker, type Application, type DisplayObject } from "pixi.js";
import cubismCoreUrl from "live2dcubismcore/live2dcubismcore.min.js?url";
import { Live2DModel } from "pixi-live2d-display/cubism4";
import type { MotionRequest } from "../stores/motionQueue";
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
}>();

const motionQueueStore = useMotionQueueStore();
let model: Live2DModelRuntime | undefined;
let disposed = false;
let lastMotionSignature: string | undefined;

Live2DModel.registerTicker(Ticker);

watch(
  () => props.app,
  async (app) => {
    if (!app) {
      return;
    }
    await ensureScript(cubismCoreUrl);
    const nextModel = (await Live2DModel.from(props.modelPath, { autoInteract: false })) as Live2DModelRuntime;
    if (disposed) {
      return;
    }
    model = nextModel;
    model.autoUpdate = true;
    model.anchor.set(0.5, 0.95);
    app.stage.addChild(model as unknown as DisplayObject);
    motionQueueStore.setMotionDefinitions(model.internalModel?.motionManager?.definitions);
    resizeModel();
  },
  { immediate: true }
);

watch(
  () => props.modelPath,
  async () => {
    if (!props.app) {
      return;
    }
    if (model) {
      props.app.stage.removeChild(model as unknown as DisplayObject);
      motionQueueStore.clearQueue();
    }
    const nextModel = (await Live2DModel.from(props.modelPath, { autoInteract: false })) as Live2DModelRuntime;
    model = nextModel;
    model.autoUpdate = true;
    model.anchor.set(0.5, 0.95);
    props.app.stage.addChild(model as unknown as DisplayObject);
    motionQueueStore.setMotionDefinitions(model.internalModel?.motionManager?.definitions);
    resizeModel();
  }
);

watch(
  () => props.canvasSize,
  () => resizeModel(),
  { deep: true }
);

watch(
  () => props.focusAt,
  (focusAt) => {
    if (!model) {
      return;
    }
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

onUnmounted(() => {
  disposed = true;
  if (model && props.app) {
    props.app.stage.removeChild(model as unknown as DisplayObject);
  }
  motionQueueStore.clearQueue();
});
</script>
