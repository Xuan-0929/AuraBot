<!--
  桌宠悬浮控制组：提供 Console、正式 Main Agent 麦克风和窗口拖动入口。
  使用透明 SVG 按钮显示真实麦克风状态，悬停、聚焦和拖动保持可操作；语音连接、媒体资源与
  clause 播放均由 PetApp 的 MainAgentRealtimeClient 持有，避免在视觉组件中复制状态机。
-->
<template>
  <div
    class="pet-quick-controls pet-vue-quick-controls"
    :data-visible="visible || isDragging ? 'true' : 'false'"
    :aria-label="t(displayLanguage, 'petQuickControls')"
    @pointerenter="emit('hoveringChange', true)"
    @pointerleave="emit('hoveringChange', false)"
    @focusin="emit('focusChange', true)"
    @focusout="handleFocusOut"
  >
    <button class="pet-action-button pet-action-console" type="button"
      :aria-label="t(displayLanguage, 'openAuraBotConsole')"
      :title="t(displayLanguage, 'openAuraBotConsole')" @click="emit('consoleOpen')">
      <svg class="pet-vue-control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z" />
      </svg>
    </button>
    <button class="pet-action-button pet-action-voice" type="button"
      :data-active="voiceInputActive ? 'true' : 'false'" :data-state="voiceInputState"
      :data-persistent="voiceInputActive || voiceInputState === 'connecting' || voiceInputState === 'error' ? 'true' : 'false'"
      :aria-pressed="voiceInputActive" :aria-busy="voiceInputState === 'connecting'"
      :aria-label="voiceLabel" :title="voiceLabel" @click="emit('voiceToggle')">
      <svg class="pet-vue-control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="9" y="2" width="6" height="12" rx="3" />
        <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" />
        <path v-if="voiceInputState === 'error'" d="m19 2 3 3m0-3-3 3" />
        <path v-else-if="!voiceInputActive && voiceInputState !== 'connecting'" d="m3 3 18 18" />
      </svg>
    </button>
    <button class="pet-action-button pet-action-drag" type="button"
      :aria-label="t(displayLanguage, 'dragPet')" :title="t(displayLanguage, 'dragPet')"
      :data-dragging="isDragging ? 'true' : 'false'"
      @pointerdown="handleDragPointerDown" @pointermove="handleDragPointerMove"
      @pointerup="handleDragPointerEnd" @pointercancel="handleDragPointerEnd"
      @lostpointercapture="handleDragPointerEnd">
      <svg class="pet-vue-control-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M12 3v18M3 12h18m-12-6 3-3 3 3m-6 12 3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { t, type DisplayLanguage } from "../../../i18n";

const props = withDefaults(
  defineProps<{
    visible: boolean;
    displayLanguage: DisplayLanguage;
    /** True only while the Main Agent microphone is capturing audio. */
    voiceInputActive?: boolean;
    /** Microphone operation state; a text-only realtime connection is off here. */
    voiceInputState?: "off" | "connecting" | "active" | "error";
  }>(),
  { voiceInputActive: false, voiceInputState: "off" }
);

const emit = defineEmits<{
  consoleOpen: [];
  voiceToggle: [];
  hoveringChange: [boolean];
  focusChange: [boolean];
  draggingChange: [boolean];
}>();

const voiceLabel = computed(() => {
  if (props.voiceInputState === "connecting") return t(props.displayLanguage, "microphoneConnecting");
  if (props.voiceInputState === "error") return t(props.displayLanguage, "microphoneErrorRetry");
  return props.voiceInputActive ? t(props.displayLanguage, "stopVoiceInput") : t(props.displayLanguage, "startVoiceInput");
});

function handleFocusOut(event: FocusEvent) {
  if (!(event.currentTarget as HTMLElement).contains(event.relatedTarget as Node | null)) emit("focusChange", false);
}

const isDragging = ref(false);
const lastDragPosition = ref<{ x: number; y: number } | undefined>(undefined);

function handleDragPointerDown(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (!target) {
    return;
  }
  event.preventDefault();
  target.setPointerCapture(event.pointerId);
  lastDragPosition.value = { x: event.screenX, y: event.screenY };
  isDragging.value = true;
  emit("draggingChange", true);
}

function handleDragPointerMove(event: PointerEvent) {
  if (!isDragging.value || !lastDragPosition.value) {
    return;
  }
  const nextPosition = { x: event.screenX, y: event.screenY };
  const delta = {
    x: nextPosition.x - lastDragPosition.value.x,
    y: nextPosition.y - lastDragPosition.value.y
  };
  lastDragPosition.value = nextPosition;
  if (delta.x !== 0 || delta.y !== 0) {
    void window.aurabot.movePetWindowBy(delta);
  }
}

function handleDragPointerEnd(event: PointerEvent) {
  const target = event.currentTarget as HTMLElement | null;
  if (target?.hasPointerCapture(event.pointerId)) {
    target.releasePointerCapture(event.pointerId);
  }
  lastDragPosition.value = undefined;
  isDragging.value = false;
  emit("draggingChange", false);
}
</script>

<style scoped>
.pet-vue-quick-controls {
  position: absolute;
  right: 14px;
  bottom: 18px;
  z-index: 5;
  display: flex;
  flex-direction: column;
  gap: 4px;
  pointer-events: none;
}
.pet-action-button {
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  box-shadow: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--aura-text);
  cursor: pointer;
  opacity: 0;
  pointer-events: none;
  transition: color 140ms ease, opacity 140ms ease;
  -webkit-app-region: no-drag;
}
.pet-vue-quick-controls[data-visible="true"] .pet-action-button,
.pet-vue-quick-controls:focus-within .pet-action-button,
.pet-action-button[data-persistent="true"] {
  opacity: 1;
  pointer-events: auto;
}
.pet-action-button:hover,
.pet-action-button[data-active="true"],
.pet-action-button[data-state="connecting"],
.pet-action-button[data-dragging="true"] { color: var(--aura-accent); background: transparent; }
.pet-action-button[data-state="error"] { color: var(--aura-danger); }
.pet-action-button:focus-visible { outline: 2px solid var(--aura-accent); outline-offset: -3px; }
.pet-vue-control-icon {
  width: 22px;
  height: 22px;
  pointer-events: none;
  filter: drop-shadow(0 0 1px #fff) drop-shadow(0 1px 1px #0008);
}
.pet-action-drag { cursor: grab; touch-action: none; }
.pet-action-drag[data-dragging="true"] { cursor: grabbing; }
@media (prefers-reduced-motion: reduce) { .pet-action-button { transition: none; } }
</style>
