<template>
  <div
    class="pet-quick-controls"
    :data-visible="visible ? 'true' : 'false'"
    :aria-label="t(displayLanguage, 'petQuickControls')"
    @pointerenter="emit('hoveringChange', true)"
    @pointerleave="emit('hoveringChange', false)"
  >
    <button
      class="pet-action-button pet-action-drag"
      type="button"
      :aria-label="t(displayLanguage, 'dragPet')"
      :data-dragging="isDragging ? 'true' : 'false'"
      @pointerdown="handleDragPointerDown"
      @pointermove="handleDragPointerMove"
      @pointerup="handleDragPointerEnd"
      @pointercancel="handleDragPointerEnd"
    >
      {{ t(displayLanguage, "petControlDragLabel") }}
    </button>
    <button
      class="pet-action-button pet-action-voice"
      type="button"
      :aria-label="t(displayLanguage, 'startVoiceInput')"
      @click="emit('voiceToggle')"
    >
      {{ t(displayLanguage, "petControlVoiceLabel") }}
    </button>
    <button
      class="pet-action-button pet-action-console"
      type="button"
      :aria-label="t(displayLanguage, 'openAuraBotConsole')"
      @click="void window.aurabot.openConsoleWindow()"
    >
      {{ t(displayLanguage, "petControlConsoleLabel") }}
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
