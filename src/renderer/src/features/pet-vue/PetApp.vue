<template>
  <main
    class="desktop-pet pet-vue-root"
    :data-agent-status="petStore.agentStatus"
    :data-click-through="petStore.petSettings.clickThroughPet ? 'true' : 'false'"
    :aria-label="t(petStore.displayLanguage, 'desktopLive2DPet')"
    @pointermove="handlePointerMove"
    @pointerleave="void clickThrough.handlePointerLeave()"
  >
    <aside v-if="petStore.petActivity" class="pet-message-bubble" :aria-label="t(petStore.displayLanguage, 'petMessage')">
      {{ petStore.petActivity.message }}
    </aside>

    <div
      class="desktop-pet-model-shell pet-vue-model-shell"
      @pointerenter="handleModelEnter"
      @pointerleave="handleModelLeave"
    >
      <div
        class="pet-vue-live2d-frame"
        :class="{ 'pet-vue-model-hovering': isHoverUiVisible }"
      >
        <Scene
          :pet-ready="petStore.isInitialized"
          :model-path="petStore.modelPath"
          :focus-at="focusAt"
          :pointer-at="pointerAt"
          @canvas-ready="handleCanvasReady"
          @interactive-area-change="(value) => (isInteractiveAreaHovering = value)"
          @model-ready="syncAgentMotion"
        >
          <TaskStickyNotes :tasks="petStore.tasks" :display-language="petStore.displayLanguage" />
        </Scene>
      </div>
      <ControlButtons
        :visible="isHoverUiVisible"
        :display-language="petStore.displayLanguage"
        :voice-input-active="isVoiceInputActive"
        @hovering-change="(value) => (isControlsHovering = value)"
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
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import type { PetResizeCorner } from "../../shared/contracts/ipc";
import { t } from "../../i18n";
import { useClickThrough } from "./composables/useClickThrough";
import { useMouseTracking } from "./composables/useMouseTracking";
import ControlButtons from "./components/ControlButtons.vue";
import TaskStickyNotes from "./components/TaskStickyNotes.vue";
import Scene from "./live2d/Scene.vue";
import { useMotionQueueStore } from "./stores/motionQueue";
import { usePetStore } from "./stores/pet";

const HOVER_EXIT_GRACE_MS = 180;

const resizeCorners: PetResizeCorner[] = ["nw", "ne", "sw", "se"];

const petStore = usePetStore();
const motionQueueStore = useMotionQueueStore();
const { focusAt } = useMouseTracking();

const isModelHovering = ref(false);
const isControlsHovering = ref(false);
/** Placeholder “mic open” state until the real voice pipeline toggles recording. */
const isVoiceInputActive = ref(false);
const isInteractiveAreaHovering = ref(false);
const isResizeGripHovering = ref(false);
const resizeDraggingCorner = ref<PetResizeCorner | null>(null);
const pointerAt = ref<{ x: number; y: number } | undefined>(undefined);
const canvasRef = ref<HTMLCanvasElement | null>(null);
let hoverExitTimeout: number | undefined;

const clickThrough = useClickThrough({
  enabled: computed(() => petStore.petSettings.clickThroughPet),
  canvasRef,
  interactiveUiHovering: computed(
    () =>
      isControlsHovering.value ||
      isInteractiveAreaHovering.value ||
      isResizeGripHovering.value ||
      resizeDraggingCorner.value !== null
  )
});

const isHoverUiVisible = computed(
  () => isModelHovering.value || isControlsHovering.value
);

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

onMounted(async () => {
  await petStore.initialize();
  unsubscribeIpc = petStore.subscribeToIpc();
});

onUnmounted(() => {
  if (hoverExitTimeout) {
    window.clearTimeout(hoverExitTimeout);
  }
  unsubscribeIpc?.();
});

let unsubscribeIpc: (() => void) | undefined;

function handleModelEnter() {
  if (hoverExitTimeout) {
    window.clearTimeout(hoverExitTimeout);
    hoverExitTimeout = undefined;
  }
  isModelHovering.value = true;
}

function handleModelLeave() {
  if (hoverExitTimeout) {
    window.clearTimeout(hoverExitTimeout);
  }
  hoverExitTimeout = window.setTimeout(() => {
    isModelHovering.value = false;
    hoverExitTimeout = undefined;
  }, HOVER_EXIT_GRACE_MS);
}

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

.pet-vue-model-hovering {
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
