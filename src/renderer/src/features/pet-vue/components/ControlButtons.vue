<template>
  <div
    class="pet-quick-controls pet-vue-quick-controls"
    :data-visible="visible ? 'true' : 'false'"
    :aria-label="t(displayLanguage, 'petQuickControls')"
    @pointerenter="emit('hoveringChange', true)"
    @pointerleave="emit('hoveringChange', false)"
  >
    <button
      class="pet-action-button pet-vue-circle-btn pet-action-console"
      type="button"
      :aria-label="t(displayLanguage, 'openAuraBotConsole')"
      @click="void window.aurabot.openConsoleWindow()"
    >
      <svg
        class="pet-vue-control-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.75"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <rect x="3" y="4" width="18" height="14" rx="2" />
        <path d="M7 8h6M7 12h4" />
      </svg>
    </button>
    <button
      class="pet-action-button pet-vue-circle-btn pet-action-voice"
      type="button"
      :aria-label="t(displayLanguage, 'startVoiceInput')"
      @click="emit('voiceToggle')"
    >
      <svg
        class="pet-vue-control-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.75"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <path d="M12 14a3 3 0 0 0 3-3V7a3 3 0 1 0-6 0v4a3 3 0 0 0 3 3Z" />
        <path d="M8 11v.5a4 4 0 0 0 8 0V11" />
        <path d="M12 18v2" />
      </svg>
    </button>
    <button
      class="pet-action-button pet-vue-circle-btn pet-action-drag"
      type="button"
      :aria-label="t(displayLanguage, 'dragPet')"
      :data-dragging="isDragging ? 'true' : 'false'"
      @pointerdown="handleDragPointerDown"
      @pointermove="handleDragPointerMove"
      @pointerup="handleDragPointerEnd"
      @pointercancel="handleDragPointerEnd"
    >
      <svg
        class="pet-vue-control-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.75"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <circle cx="9" cy="9" r="1.35" fill="currentColor" stroke="none" />
        <circle cx="15" cy="9" r="1.35" fill="currentColor" stroke="none" />
        <circle cx="9" cy="15" r="1.35" fill="currentColor" stroke="none" />
        <circle cx="15" cy="15" r="1.35" fill="currentColor" stroke="none" />
      </svg>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { t, type DisplayLanguage } from "../../../i18n";

defineProps<{
  visible: boolean;
  displayLanguage: DisplayLanguage;
}>();

const emit = defineEmits<{
  voiceToggle: [];
  hoveringChange: [boolean];
}>();

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
}
</script>

<style scoped>
.pet-vue-quick-controls.pet-quick-controls {
  left: auto;
  right: 14px;
  bottom: 18px;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: none;
  border-radius: 0;
  background: none;
  box-shadow: none;
  backdrop-filter: none;
  transform: translate(12px, 0);
  transition:
    opacity 180ms ease,
    transform 180ms ease;
}

.pet-vue-quick-controls[data-visible="true"] {
  transform: translate(0, 0);
}

.pet-vue-circle-btn {
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.pet-vue-circle-btn .pet-vue-control-icon {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  pointer-events: none;
}

.pet-vue-circle-btn.pet-action-drag {
  cursor: grab;
  touch-action: none;
}

.pet-vue-circle-btn.pet-action-drag[data-dragging="true"] {
  cursor: grabbing;
  transform: translateY(0);
  border-color: rgba(249, 115, 22, 0.45);
  box-shadow:
    0 8px 16px rgba(249, 115, 22, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.5);
}

.pet-vue-circle-btn:hover,
.pet-vue-circle-btn[data-active="true"] {
  transform: translateY(-1px);
}

.pet-vue-circle-btn.pet-action-drag[data-dragging="true"]:hover {
  transform: translateY(0);
}

.pet-vue-circle-btn:active:not(.pet-action-drag) {
  transform: translateY(0);
  box-shadow:
    0 4px 10px rgba(123, 96, 81, 0.1),
    inset 0 1px 0 rgba(255, 255, 255, 0.45);
}
</style>
