<template>
  <div
    class="pet-quick-controls pet-vue-quick-controls"
    :data-visible="visible ? 'true' : 'false'"
    :aria-label="t(displayLanguage, 'petQuickControls')"
    @pointerenter="emit('hoveringChange', true)"
    @pointerleave="emit('hoveringChange', false)"
  >
    <div class="pet-vue-liquid-glass">
      <LiquidGlass
        :padding="'0'"
        :corner-radius="22"
        :blur-amount="0.095"
        :displacement-scale="48"
        :saturation="148"
        :aberration-intensity="1.45"
        :elasticity="0.1"
        :style="liquidGlassSurfaceStyle"
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
            stroke-width="1.9"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path
              d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"
            />
          </svg>
        </button>
      </LiquidGlass>
    </div>
    <div class="pet-vue-liquid-glass">
      <LiquidGlass
        :padding="'0'"
        :corner-radius="22"
        :blur-amount="0.095"
        :displacement-scale="48"
        :saturation="148"
        :aberration-intensity="1.45"
        :elasticity="0.1"
        :style="liquidGlassSurfaceStyle"
      >
        <button
          class="pet-action-button pet-vue-circle-btn pet-action-voice"
          type="button"
          :data-active="voiceInputActive ? 'true' : 'false'"
          :aria-label="t(displayLanguage, 'startVoiceInput')"
          @click="emit('voiceToggle')"
        >
          <svg
            class="pet-vue-control-icon pet-vue-mic-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.9"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path d="M12 14a3 3 0 0 0 3-3V7a3 3 0 1 0-6 0v4a3 3 0 0 0 3 3Z" />
            <path d="M8 11v.5a4 4 0 0 0 8 0V11" />
            <path d="M12 18v2" />
          </svg>
        </button>
      </LiquidGlass>
    </div>
    <div class="pet-vue-liquid-glass">
      <LiquidGlass
        :padding="'0'"
        :corner-radius="22"
        :blur-amount="0.095"
        :displacement-scale="48"
        :saturation="148"
        :aberration-intensity="1.45"
        :elasticity="0.1"
        :style="liquidGlassSurfaceStyle"
      >
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
            class="pet-vue-control-icon pet-vue-drag-arrows"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.9"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <!-- Four diagonal arrows from center toward corners (move affordance) -->
            <path d="M12 12 7.25 7.25M7.25 7.25 9.1 7.25M7.25 7.25 7.25 9.1" />
            <path d="M12 12 16.75 7.25M16.75 7.25 14.9 7.25M16.75 7.25 16.75 9.1" />
            <path d="M12 12 7.25 16.75M7.25 16.75 9.1 16.75M7.25 16.75 7.25 14.9" />
            <path d="M12 12 16.75 16.75M16.75 16.75 14.9 16.75M16.75 16.75 16.75 14.9" />
          </svg>
        </button>
      </LiquidGlass>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { LiquidGlass } from "@wxperia/liquid-glass-vue";
import { t, type DisplayLanguage } from "../../../i18n";

withDefaults(
  defineProps<{
    visible: boolean;
    displayLanguage: DisplayLanguage;
    /** When true, mic is “open” (listening); dims when false. Placeholder until voice pipeline exists. */
    voiceInputActive?: boolean;
  }>(),
  { voiceInputActive: false }
);

const emit = defineEmits<{
  voiceToggle: [];
  hoveringChange: [boolean];
}>();

/**
 * LiquidGlass: overlay layers use `top: style.top || "50%"` + `translate(-50%, -50%)`. The inner
 * `GlassContainer` only receives `baseStyle` (merged `style` + transform), not `positionStyles`, so
 * our `style` must include the same `top`/`left` as those defaults or the button box and frosted
 * layers use different anchors. Never pass `top`/`left: "auto"` — they are truthy and skip the "50%"
 * fallback while translate still runs.
 */
const liquidGlassSurfaceStyle = {
  position: "relative" as const,
  top: "50%",
  left: "50%",
  zIndex: 1
};

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
  position: absolute;
  left: auto;
  right: 14px;
  bottom: 18px;
  z-index: 5;
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

.pet-vue-liquid-glass {
  position: relative;
  flex-shrink: 0;
  line-height: 0;
  isolation: isolate;
  width: 44px;
  height: 44px;
  display: block;
  border-radius: 50%;
  overflow: hidden;
}

.pet-vue-liquid-glass :deep(.text-white) {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: 0;
  min-height: 0;
  color: var(--aura-ink);
}

.pet-vue-liquid-glass :deep(.glass) {
  box-sizing: border-box;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  gap: 0;
  border: 1px solid rgba(255, 255, 255, 0.28);
  box-shadow: 0 4px 14px rgba(55, 42, 34, 0.08);
}

.pet-vue-liquid-glass :deep(div.relative) {
  width: 100%;
  height: 100%;
}

.pet-vue-circle-btn {
  width: 44px;
  height: 44px;
  min-width: 44px;
  min-height: 44px;
  padding: 0;
  border-radius: 50%;
  border: none;
  background: transparent;
  box-shadow: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--aura-ink);
  -webkit-app-region: no-drag;
}

.pet-vue-circle-btn .pet-vue-control-icon {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  pointer-events: none;
  filter: drop-shadow(0 0.5px 0 rgba(255, 255, 255, 0.55))
    drop-shadow(0 1px 1.5px rgba(45, 32, 24, 0.35));
}

.pet-vue-circle-btn.pet-action-drag {
  cursor: grab;
  touch-action: none;
}

.pet-vue-circle-btn.pet-action-drag[data-dragging="true"] {
  cursor: grabbing;
  outline: 2px solid rgba(249, 115, 22, 0.45);
  outline-offset: 1px;
}

.pet-vue-circle-btn:hover:not(:disabled):not(.pet-action-voice),
.pet-vue-circle-btn[data-active="true"]:not(.pet-action-voice) {
  filter: brightness(1.04);
}

.pet-action-voice .pet-vue-mic-icon {
  transition:
    opacity 180ms ease,
    filter 180ms ease;
}

/* Voice off: dimmer; voice on: full contrast + slight lift (brightness). */
.pet-action-voice[data-active="false"] .pet-vue-mic-icon {
  opacity: 0.52;
  filter: brightness(0.78);
}

.pet-action-voice[data-active="true"] .pet-vue-mic-icon {
  opacity: 1;
  filter: drop-shadow(0 0.5px 0 rgba(255, 255, 255, 0.55))
    drop-shadow(0 1px 1.5px rgba(45, 32, 24, 0.35)) brightness(1.14);
}

.pet-vue-circle-btn:active:not(.pet-action-drag):not(:disabled) {
  filter: brightness(0.97);
}
</style>
