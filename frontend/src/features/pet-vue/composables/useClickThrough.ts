/** Coordinates Electron transparent-window hit testing with the pet canvas and controls.
 * Pointer movement/leave and interaction changes reuse the existing IPC; active captures
 * keep controls interactive until release. Settings changes invalidate cached native state,
 * and disposal restores normal mouse handling. */
import { computed, onUnmounted, ref, watch, type Ref } from "vue";

interface UseClickThroughOptions {
  enabled: Ref<boolean>;
  canvasRef: Ref<HTMLCanvasElement | null>;
  interactiveUiHovering: Ref<boolean>;
}

interface PetMousePassthroughState {
  enabled: boolean;
  canvasReady: boolean;
  interactiveUiHovering: boolean;
  resizeUiHovering: boolean;
}

export function resolvePetMousePassthroughState(state: PetMousePassthroughState): boolean | undefined {
  if (!state.enabled || !state.canvasReady) {
    return undefined;
  }
  if (state.interactiveUiHovering || state.resizeUiHovering) {
    return false;
  }
  return true;
}

export function useClickThrough(options: UseClickThroughOptions) {
  const isPassthrough = ref<boolean | undefined>(undefined);
  const isReady = computed(() => options.enabled.value && Boolean(options.canvasRef.value));

  async function handlePointerMove() {
    const nextValue = resolvePetMousePassthroughState({
      canvasReady: Boolean(options.canvasRef.value),
      enabled: options.enabled.value,
      interactiveUiHovering: options.interactiveUiHovering.value,
      resizeUiHovering: options.interactiveUiHovering.value
    });
    if (nextValue === undefined) {
      return;
    }
    await setPassthrough(nextValue);
  }

  async function handlePointerLeave() {
    if (!isReady.value) {
      return;
    }
    await setPassthrough(!options.interactiveUiHovering.value);
  }

  async function setPassthrough(nextValue: boolean) {
    if (isPassthrough.value === nextValue) {
      return;
    }
    isPassthrough.value = nextValue;
    await window.aurabot.setPetMousePassthrough(nextValue);
  }

  watch(options.enabled, () => {
    isPassthrough.value = undefined;
    void handlePointerMove();
  });

  watch(options.interactiveUiHovering, () => { void handlePointerMove(); });

  onUnmounted(() => {
    if (isPassthrough.value !== undefined) {
      void window.aurabot.setPetMousePassthrough(false);
    }
    isPassthrough.value = undefined;
  });

  return {
    handlePointerMove,
    handlePointerLeave,
    setPassthrough
  };
}
