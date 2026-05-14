<template>
  <div class="live2d-vue-model-anchor" aria-hidden="true" />
</template>

<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import type { Application } from "pixi.js";
import { calculateLive2DPlacement } from "./live2dPlacement";
import { useMotionQueueStore } from "../stores/motionQueue";
import { loadLive2DModel, removeAndDestroyLive2DModel, type Live2DModelRuntime } from "../../live2d/runtime";

interface FocusPoint {
  x: number;
  y: number;
}

interface PointerPoint {
  x: number;
  y: number;
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

function teardownModelFromStage(app: Application | null) {
  removeAndDestroyLive2DModel(app, model);
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

    const nextModel = await loadLive2DModel(modelPath);
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

onUnmounted(() => {
  disposed = true;
  loadGeneration += 1;
  teardownModelFromStage(props.app);
});
</script>
