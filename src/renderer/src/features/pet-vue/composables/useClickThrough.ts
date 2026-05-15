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
  overResizeCorner: boolean;
}

export function resolvePetMousePassthroughState(state: PetMousePassthroughState): boolean | undefined {
  if (!state.enabled || !state.canvasReady) {
    return undefined;
  }
  if (state.interactiveUiHovering || state.overResizeCorner) {
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
      overResizeCorner: isPointerOverWindowCornerResizeZone(event.clientX, event.clientY)
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

/** Margin from each window edge for combined corner zones (matches `.pet-vue-resize-grip` hit targets). */
const CORNER_RESIZE_MARGIN_PX = 32;

function isPointerOverWindowCornerResizeZone(clientX: number, clientY: number): boolean {
  const w = window.innerWidth;
  const h = window.innerHeight;
  const m = CORNER_RESIZE_MARGIN_PX;
  const nearLeft = clientX < m;
  const nearRight = clientX > w - m;
  const nearTop = clientY < m;
  const nearBottom = clientY > h - m;
  return (nearLeft || nearRight) && (nearTop || nearBottom);
}
