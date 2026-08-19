<!--
  桌宠窗口：组合 Live2D 舞台、主动消息气泡、控制按钮与窗口缩放交互。
  它从共享 store 读取聊天/Memory 驱动的宠物状态，不渲染或控制任何 Task 数据；
  Live2D 运动与窗口交互仍分别委托给 motion store 和 preload IPC。
-->
<template>
  <main
    class="desktop-pet pet-vue-root"
    :data-agent-status="petStore.agentStatus"
    :data-click-through="petStore.petSettings.clickThroughPet ? 'true' : 'false'"
    :data-resizing="resizeDraggingDirection ? 'true' : 'false'"
    :aria-label="t(petStore.displayLanguage, 'desktopLive2DPet')"
    @pointermove="handlePointerMove"
    @pointerleave="void clickThrough.handlePointerLeave()"
    @wheel="handleWheel"
  >
    <aside v-if="petStore.petActivity" class="pet-message-bubble" :aria-label="t(petStore.displayLanguage, 'petMessage')">
      {{ petStore.petActivity.message }}
    </aside>

    <div class="desktop-pet-model-shell pet-vue-model-shell" data-testid="live2d-model-surface">
      <div class="pet-vue-live2d-frame">
        <Scene
          :pet-ready="petStore.isInitialized"
          :model-path="petStore.modelPath"
          :focus-at="petStore.focusAt"
          :pointer-at="pointerAt"
          :view-transform="live2dViewTransform"
          :zoom-request="live2dZoomRequest"
          @canvas-ready="handleCanvasReady"
          @interactive-area-change="(value) => (isInteractiveAreaHovering = value)"
          @model-ready="syncAgentMotion"
          @view-transform-change="handleLive2DViewTransformChange"
        />
      </div>
      <ControlButtons
        :visible="true"
        :display-language="petStore.displayLanguage"
        :voice-input-active="isVoiceInputActive"
        @hovering-change="(value) => (isControlsHovering = value)"
        @console-open="() => void petStore.openConsoleWindow()"
        @voice-toggle="handleVoiceToggle"
      />
    </div>

    <div
      class="pet-vue-resize-grips"
      aria-hidden="true"
      @pointerenter="isResizeGripHovering = true"
      @pointerleave="onResizeGripsPointerLeave"
    >
      <div
        v-for="direction in resizeDirections"
        :key="direction"
        :class="['pet-vue-resize-grip', `pet-vue-resize-grip--${direction}`]"
        @pointerdown="(e) => handleResizePointerDown(direction, e)"
        @pointermove="(e) => handleResizePointerMove(direction, e)"
        @pointerup="handleResizePointerEnd"
        @pointercancel="handleResizePointerEnd"
      />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { PetResizeDirection } from "@shared/contracts/ipc";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import {
  readStoredLive2DViewTransform,
  writeStoredLive2DViewTransform
} from "../live2d/live2dViewScale";
import type { Live2DViewTransform } from "../live2d/live2dDiagnostics";
import { useClickThrough } from "./composables/useClickThrough";
import ControlButtons from "./components/ControlButtons.vue";
import Scene from "./live2d/Scene.vue";
import { useMotionQueueStore } from "./stores/motionQueue";

const resizeDirections: PetResizeDirection[] = ["n", "s", "e", "w", "nw", "ne", "sw", "se"];

const petStore = useAuraBotStore();
const motionQueueStore = useMotionQueueStore();

const isControlsHovering = ref(false);
/** Placeholder “mic open” state until the real voice pipeline toggles recording. */
const isVoiceInputActive = ref(false);
const isInteractiveAreaHovering = ref(false);
const isResizeGripHovering = ref(false);
const resizeDraggingDirection = ref<PetResizeDirection | null>(null);
const pointerAt = ref<{ x: number; y: number } | undefined>(undefined);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const live2dViewTransform = ref(readStoredLive2DViewTransform(window.localStorage));
const live2dZoomRequest = ref<{ id: number; deltaY: number; focalPoint: { x: number; y: number } } | undefined>(undefined);

const clickThrough = useClickThrough({
  enabled: computed(() => petStore.petSettings.clickThroughPet),
  canvasRef,
  interactiveUiHovering: computed(
    () => isControlsHovering.value || isResizeGripHovering.value || resizeDraggingDirection.value !== null
  )
});

watch(
  () => petStore.petActivity?.id,
  (activityId) => {
    if (!activityId) {
      return;
    }
    petStore.armPetActivityAutoClear(activityId);
  },
  { immediate: true }
);

let lastRequestedMotionSignature: string | undefined;

function syncAgentMotion() {
  const motion = motionQueueStore.defaultIdleMotion;
  if (!motion) {
    return;
  }
  lastRequestedMotionSignature = `${motion.group}:${motion.index ?? "default"}`;
  void motionQueueStore.requestMotion(motion.group, motion.index);
}

watch(
  () => petStore.modelPath,
  () => {
    lastRequestedMotionSignature = undefined;
  }
);

watch(
  () => [petStore.agentStatus, motionQueueStore.defaultIdleMotion] as const,
  async ([, idleMotion]) => {
    if (!idleMotion) {
      return;
    }
    const signature = `${idleMotion.group}:${idleMotion.index ?? "default"}`;
    // Agent status no longer changes the ambient clip (always Idle), but this
    // still dedupes against syncAgentMotion and avoids queue churn when the
    // orchestrator repeats the same status.
    if (lastRequestedMotionSignature === signature) {
      return;
    }
    lastRequestedMotionSignature = signature;
    await motionQueueStore.requestMotion(idleMotion.group, idleMotion.index);
  },
  { immediate: true }
);

function handleVoiceToggle() {
  isVoiceInputActive.value = !isVoiceInputActive.value;
  // Reserved interface: voice pipeline will be implemented in a later phase.
  console.info("[pet-vue] voice toggle reserved");
}

function handleCanvasReady(canvas: HTMLCanvasElement) {
  canvasRef.value = canvas;
}

function onResizeGripsPointerLeave() {
  if (resizeDraggingDirection.value !== null) {
    return;
  }
  isResizeGripHovering.value = false;
}

function handleResizePointerDown(direction: PetResizeDirection, event: PointerEvent) {
  event.preventDefault();
  void clickThrough.setPassthrough(false);
  const target = event.currentTarget as HTMLElement | null;
  if (!target) {
    return;
  }
  target.setPointerCapture(event.pointerId);
  resizeDraggingDirection.value = direction;
}

function handleResizePointerMove(direction: PetResizeDirection, event: PointerEvent) {
  if (resizeDraggingDirection.value !== direction) {
    return;
  }
  void window.aurabot.resizePetWindowCorner(direction, {
    x: event.movementX,
    y: event.movementY
  });
}

function handleResizePointerEnd(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (target?.hasPointerCapture(event.pointerId)) {
    target.releasePointerCapture(event.pointerId);
  }
  resizeDraggingDirection.value = null;
}

function handlePointerMove(event: PointerEvent) {
  const canvas = canvasRef.value;
  if (canvas) {
    const rect = canvas.getBoundingClientRect();
    pointerAt.value = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  void clickThrough.handlePointerMove(event);
}

function handleWheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) {
    return;
  }

  event.preventDefault();
  const canvas = canvasRef.value;
  if (!canvas) {
    return;
  }

  const rect = canvas.getBoundingClientRect();
  live2dZoomRequest.value = {
    id: Date.now(),
    deltaY: event.deltaY,
    focalPoint: {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top
    }
  };
}

function handleLive2DViewTransformChange(transform: Live2DViewTransform) {
  live2dViewTransform.value = transform;
  writeStoredLive2DViewTransform(window.localStorage, transform);
}
</script>

<style>
.pet-vue-root {
  min-width: 270px;
  min-height: 380px;
  /* Fill the pet window so the model shell (and anchored controls) track the resizable frame. */
  place-items: stretch;
}

/* Large transparent corners — programmatic window resize via IPC (see `resizePetWindowCorner`).
 * Must stack above `.pet-quick-controls` / `.pet-hover-chat` (z-index: 4 in styles.css). */
.pet-vue-resize-grips {
  position: absolute;
  inset: 0;
  z-index: 12;
  pointer-events: none;
}

.pet-vue-resize-grip {
  position: absolute;
  box-sizing: border-box;
  pointer-events: auto;
  touch-action: none;
  background: transparent;
}

.pet-vue-resize-grip--n,
.pet-vue-resize-grip--s {
  left: 32px;
  right: 32px;
  height: 14px;
}

.pet-vue-resize-grip--e,
.pet-vue-resize-grip--w {
  top: 32px;
  bottom: 32px;
  width: 14px;
}

.pet-vue-resize-grip--nw,
.pet-vue-resize-grip--ne,
.pet-vue-resize-grip--sw,
.pet-vue-resize-grip--se {
  width: 32px;
  height: 32px;
}

.pet-vue-resize-grip--n {
  top: 0;
  cursor: ns-resize;
}

.pet-vue-resize-grip--s {
  bottom: 0;
  cursor: ns-resize;
}

.pet-vue-resize-grip--e {
  right: 0;
  cursor: ew-resize;
}

.pet-vue-resize-grip--w {
  left: 0;
  cursor: ew-resize;
}

.pet-vue-resize-grip--nw {
  top: 0;
  left: 0;
  cursor: nwse-resize;
}

.pet-vue-resize-grip--ne {
  top: 0;
  right: 0;
  cursor: nesw-resize;
}

.pet-vue-resize-grip--sw {
  bottom: 0;
  left: 0;
  cursor: nesw-resize;
}

.pet-vue-resize-grip--se {
  right: 0;
  bottom: 0;
  cursor: nwse-resize;
}

.pet-vue-model-shell {
  /* Inset ring avoids clipping from .desktop-pet overflow:hidden (outline showed corners only). */
  border-radius: 22px;
  box-shadow: inset 0 0 0 2px transparent;
  transition: box-shadow 180ms ease;
}

.pet-vue-live2d-frame {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 0;
  transition: opacity 180ms ease;
}

.pet-vue-root:hover .pet-vue-model-shell {
  box-shadow: inset 0 0 0 2px #f97316;
}

.pet-vue-root[data-resizing="false"]:hover:not(:has(.pet-vue-resize-grip:hover)) .pet-vue-live2d-frame {
  opacity: 0.4;
}

.pet-vue-model-shell .desktop-pet-stage,
.pet-vue-model-shell .live2d-canvas-host,
.pet-vue-model-shell .live2d-canvas {
  pointer-events: none;
}

.pet-quick-controls {
  pointer-events: auto;
}
</style>
