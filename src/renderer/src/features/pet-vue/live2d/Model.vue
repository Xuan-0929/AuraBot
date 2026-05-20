<template>
  <div class="live2d-vue-model-anchor" aria-hidden="true" />
</template>

<script setup lang="ts">
import { onUnmounted, watch } from "vue";
import type { Application } from "pixi.js";
import { calculateLive2DPlacement } from "./live2dPlacement";
import type { Live2DSize, Live2DViewTransform } from "../../live2d/live2dDiagnostics";
import { calculateFocusedLive2DViewTransform } from "../../live2d/live2dViewScale";
import { useMotionQueueStore } from "../stores/motionQueue";
import {
  loadLive2DModel,
  normalizeLive2DHitAreaNames,
  removeAndDestroyLive2DModel,
  type Live2DModelRuntime
} from "../../live2d/runtime";

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
  viewTransform: Live2DViewTransform;
  zoomRequest?: { id: number; deltaY: number; focalPoint: { x: number; y: number } };
}>();

const emit = defineEmits<{
  interactiveChange: [boolean];
  modelReady: [];
  viewTransformChange: [Live2DViewTransform];
}>();

const motionQueueStore = useMotionQueueStore();
const LIVE2D_MOTION_PRIORITY_IDLE = 1;
const LIVE2D_MOTION_PRIORITY_NORMAL = 2;
let model: Live2DModelRuntime | undefined;
let disposed = false;
let lastMotionSignature: string | undefined;
let loadGeneration = 0;
let lastCanvasW = 0;
let lastCanvasH = 0;
let naturalModelSize: Live2DSize | undefined;

function teardownModelFromStage(app: Application | null) {
  removeAndDestroyLive2DModel(app, model);
  model = undefined;
  naturalModelSize = undefined;
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
    naturalModelSize = resolveNaturalModelSize(model, {
      width: Math.max(1, Math.round(props.canvasSize.width)),
      height: Math.max(1, Math.round(props.canvasSize.height))
    });
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
  () => props.viewTransform,
  () => resizeModel()
);

watch(
  () => props.zoomRequest,
  (request) => {
    if (!request || !model || !naturalModelSize) {
      return;
    }
    emit(
      "viewTransformChange",
      calculateFocusedLive2DViewTransform({
        hostSize: resolveHostSize(),
        modelSize: naturalModelSize,
        compact: false,
        current: props.viewTransform,
        focalPoint: request.focalPoint,
        wheelDeltaY: request.deltaY
      })
    );
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
      const priority = motion.group === "Idle" ? LIVE2D_MOTION_PRIORITY_IDLE : LIVE2D_MOTION_PRIORITY_NORMAL;
      const result = model.motion(motion.group, motion.index, priority);
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
    const areaNames = normalizeLive2DHitAreaNames(model.internalModel?.hitAreas);
    if (areaNames.length === 0) {
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
  const hostSize = resolveHostSize();
  const modelSize = naturalModelSize ?? resolveNaturalModelSize(model, hostSize);
  const placement = calculateLive2DPlacement({
    hostSize,
    modelSize,
    compact: false,
    viewTransform: props.viewTransform
  });
  model.scale.set(placement.scale);
  model.x = placement.x;
  model.y = placement.y;
}

function resolveHostSize(): Live2DSize {
  return {
    width: Math.max(1, Math.round(props.canvasSize.width)),
    height: Math.max(1, Math.round(props.canvasSize.height))
  };
}

function resolveNaturalModelSize(model: Live2DModelRuntime, fallbackSize: Live2DSize): Live2DSize {
  return {
    width: Math.max(1, Math.round(model.internalModel?.width ?? model.width ?? fallbackSize.width)),
    height: Math.max(1, Math.round(model.internalModel?.height ?? model.height ?? fallbackSize.height))
  };
}

onUnmounted(() => {
  disposed = true;
  loadGeneration += 1;
  teardownModelFromStage(props.app);
});
</script>
