<template>
  <main
    class="desktop-pet pet-vue-root"
    :data-agent-status="petStore.agentStatus"
    :data-click-through="petStore.petSettings.clickThroughPet ? 'true' : 'false'"
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
          :view-scale="live2dViewScale"
          @canvas-ready="handleCanvasReady"
          @interactive-area-change="(value) => (isInteractiveAreaHovering = value)"
          @model-ready="syncAgentMotion"
        >
          <TaskStickyNotes :tasks="petStore.tasks" :display-language="petStore.displayLanguage" />
        </Scene>
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
        v-for="corner in resizeCorners"
        :key="corner"
        :class="['pet-vue-resize-grip', `pet-vue-resize-grip--${corner}`]"
        @pointerdown="(e) => handleResizePointerDown(corner, e)"
        @pointermove="(e) => handleResizePointerMove(corner, e)"
        @pointerup="handleResizePointerEnd"
        @pointercancel="handleResizePointerEnd"
      />
    </div>
  </main>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { PetResizeCorner } from "../../shared/contracts/ipc";
import { useAuraBotStore } from "../../app/store";
import { t } from "../../i18n";
import {
  calculateNextLive2DViewScale,
  readStoredLive2DViewScale,
  writeStoredLive2DViewScale
} from "../live2d/live2dViewScale";
import { useClickThrough } from "./composables/useClickThrough";
import ControlButtons from "./components/ControlButtons.vue";
import TaskStickyNotes from "./components/TaskStickyNotes.vue";
import Scene from "./live2d/Scene.vue";
import { useMotionQueueStore } from "./stores/motionQueue";

const resizeCorners: PetResizeCorner[] = ["nw", "ne", "sw", "se"];

const petStore = useAuraBotStore();
const motionQueueStore = useMotionQueueStore();

const isControlsHovering = ref(false);
/** Placeholder “mic open” state until the real voice pipeline toggles recording. */
const isVoiceInputActive = ref(false);
const isInteractiveAreaHovering = ref(false);
const isResizeGripHovering = ref(false);
const resizeDraggingCorner = ref<PetResizeCorner | null>(null);
const pointerAt = ref<{ x: number; y: number } | undefined>(undefined);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const live2dViewScale = ref(readStoredLive2DViewScale(window.localStorage));

const clickThrough = useClickThrough({
  enabled: computed(() => petStore.petSettings.clickThroughPet),
  canvasRef,
  interactiveUiHovering: computed(
    () => isControlsHovering.value || isResizeGripHovering.value || resizeDraggingCorner.value !== null
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
  if (resizeDraggingCorner.value !== null) {
    return;
  }
  isResizeGripHovering.value = false;
}

function handleResizePointerDown(corner: PetResizeCorner, event: PointerEvent) {
  event.preventDefault();
  void clickThrough.setPassthrough(false);
  const target = event.currentTarget as HTMLElement | null;
  if (!target) {
    return;
  }
  target.setPointerCapture(event.pointerId);
  resizeDraggingCorner.value = corner;
}

function handleResizePointerMove(corner: PetResizeCorner, event: PointerEvent) {
  if (resizeDraggingCorner.value !== corner) {
    return;
  }
  void window.aurabot.resizePetWindowCorner(corner, {
    x: event.movementX,
    y: event.movementY
  });
}

function handleResizePointerEnd(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (target?.hasPointerCapture(event.pointerId)) {
    target.releasePointerCapture(event.pointerId);
  }
  resizeDraggingCorner.value = null;
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
  const nextScale = calculateNextLive2DViewScale(live2dViewScale.value, event.deltaY);
  if (nextScale === live2dViewScale.value) {
    return;
  }

  live2dViewScale.value = nextScale;
  writeStoredLive2DViewScale(window.localStorage, nextScale);
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
  width: 32px;
  height: 32px;
  box-sizing: border-box;
  pointer-events: auto;
  touch-action: none;
  background: transparent;
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

.pet-vue-root:hover .pet-vue-live2d-frame {
  opacity: 0.4;
}

.pet-vue-model-shell .desktop-pet-stage,
.pet-vue-model-shell .live2d-canvas-host,
.pet-vue-model-shell .live2d-canvas {
  pointer-events: none;
}

.pet-vue-model-shell .task-sticky-notes {
  pointer-events: auto;
}

.pet-quick-controls {
  pointer-events: auto;
}
</style>
