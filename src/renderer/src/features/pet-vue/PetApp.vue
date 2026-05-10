<template>
  <main
    class="desktop-pet pet-vue-root"
    :data-agent-status="petStore.agentStatus"
    :data-click-through="petStore.petSettings.clickThroughPet ? 'true' : 'false'"
    :aria-label="t(petStore.displayLanguage, 'desktopLive2DPet')"
    @pointermove="handlePointerMove"
    @pointerleave="void clickThrough.handlePointerLeave()"
    @pointerenter="isModelHovering = true"
  >
    <aside v-if="petStore.petActivity" class="pet-message-bubble" :aria-label="t(petStore.displayLanguage, 'petMessage')">
      {{ petStore.petActivity.message }}
    </aside>

    <div
      class="desktop-pet-model-shell pet-vue-model-shell"
      :class="{ 'pet-vue-model-hovering': isHoverUiVisible }"
      @pointerenter="handleModelEnter"
      @pointerleave="handleModelLeave"
    >
      <Scene
        :model-path="petStore.modelPath"
        :focus-at="focusAt"
        :pointer-at="pointerAt"
        @canvas-ready="handleCanvasReady"
        @interactive-area-change="(value) => (isInteractiveAreaHovering = value)"
      >
        <TaskStickyNotes :tasks="petStore.tasks" :display-language="petStore.displayLanguage" />
      </Scene>
    </div>

    <HoverChat
      v-if="isHoverUiVisible"
      :side="hoverChatSide"
      :display-language="petStore.displayLanguage"
      @send="handleSendText"
      @hovering-change="(value) => (isHoverChatHovering = value)"
      @focus-change="(value) => (isHoverChatFocused = value)"
    />

    <ControlButtons
      :visible="isHoverUiVisible"
      :display-language="petStore.displayLanguage"
      @hovering-change="(value) => (isControlsHovering = value)"
      @voice-toggle="handleVoiceToggle"
    />
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { t } from "../../i18n";
import { getLive2DMotionForAgentStatus } from "../live2d/live2dStatus";
import { useClickThrough } from "./composables/useClickThrough";
import { useMouseTracking } from "./composables/useMouseTracking";
import ControlButtons from "./components/ControlButtons.vue";
import HoverChat from "./components/HoverChat.vue";
import TaskStickyNotes from "./components/TaskStickyNotes.vue";
import Scene from "./live2d/Scene.vue";
import { useMotionQueueStore } from "./stores/motionQueue";
import { usePetStore } from "./stores/pet";

const HOVER_CHAT_WIDTH_PX = 224;
const HOVER_CHAT_GAP_PX = 12;
const HOVER_EXIT_GRACE_MS = 180;

const petStore = usePetStore();
const motionQueueStore = useMotionQueueStore();
const { focusAt } = useMouseTracking();

const isModelHovering = ref(false);
const isControlsHovering = ref(false);
const isHoverChatHovering = ref(false);
const isHoverChatFocused = ref(false);
const isInteractiveAreaHovering = ref(false);
const pointerAt = ref<{ x: number; y: number } | undefined>(undefined);
const hoverChatSide = ref<"left" | "right">("left");
const canvasRef = ref<HTMLCanvasElement | null>(null);
let hoverExitTimeout: number | undefined;

const clickThrough = useClickThrough({
  enabled: computed(() => petStore.petSettings.clickThroughPet),
  canvasRef,
  interactiveUiHovering: computed(
    () => isControlsHovering.value || isHoverChatHovering.value || isHoverChatFocused.value || isInteractiveAreaHovering.value
  )
});

const isHoverUiVisible = computed(
  () => isModelHovering.value || isControlsHovering.value || isHoverChatHovering.value || isHoverChatFocused.value
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

watch(
  () => petStore.agentStatus,
  async (status) => {
    const motion = getLive2DMotionForAgentStatus(status);
    await motionQueueStore.requestMotion(motion.group, motion.index);
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

function handleModelEnter(event: PointerEvent) {
  if (hoverExitTimeout) {
    window.clearTimeout(hoverExitTimeout);
    hoverExitTimeout = undefined;
  }
  updateHoverChatSide(event.currentTarget as HTMLElement | null);
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

function updateHoverChatSide(host: HTMLElement | null) {
  const modelRect = host?.getBoundingClientRect();
  if (!modelRect) {
    hoverChatSide.value = "left";
    return;
  }
  const requiredLeftWidth = HOVER_CHAT_WIDTH_PX + HOVER_CHAT_GAP_PX;
  hoverChatSide.value = modelRect.left >= requiredLeftWidth ? "left" : "right";
}

async function handleSendText(content: string) {
  await petStore.sendTextInput(content);
}

function handleVoiceToggle() {
  // Reserved interface: voice pipeline will be implemented in a later phase.
  console.info("[pet-vue] voice toggle reserved");
}

function handleCanvasReady(canvas: HTMLCanvasElement) {
  canvasRef.value = canvas;
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
  resize: both;
}

.pet-vue-model-shell {
  outline: 2px solid transparent;
  border-radius: 22px;
  transition: opacity 180ms ease, outline-color 180ms ease;
}

.pet-vue-model-hovering {
  opacity: 0.4;
  outline-color: #f97316;
}

.pet-vue-model-shell .desktop-pet-stage,
.pet-vue-model-shell .live2d-canvas-host,
.pet-vue-model-shell .live2d-canvas {
  pointer-events: none;
}

.pet-vue-model-shell .task-sticky-notes {
  pointer-events: auto;
}

.pet-hover-chat,
.pet-quick-controls {
  pointer-events: auto;
}
</style>
