import { computed, onUnmounted, ref, type Ref } from "vue";

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

  async function handlePointerMove(event: PointerEvent) {
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
    await setPassthrough(true);
  }

  async function setPassthrough(nextValue: boolean) {
    if (isPassthrough.value === nextValue) {
      return;
    }
    isPassthrough.value = nextValue;
    await window.aurabot.setPetMousePassthrough(nextValue);
  }

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
