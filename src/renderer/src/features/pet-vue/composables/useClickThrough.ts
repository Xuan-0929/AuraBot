import { computed, onUnmounted, ref, type Ref } from "vue";

interface UseClickThroughOptions {
  enabled: Ref<boolean>;
  canvasRef: Ref<HTMLCanvasElement | null>;
  interactiveUiHovering: Ref<boolean>;
}

export function useClickThrough(options: UseClickThroughOptions) {
  const isPassthrough = ref<boolean | undefined>(undefined);
  const isReady = computed(() => options.enabled.value && Boolean(options.canvasRef.value));

  async function handlePointerMove(event: PointerEvent) {
    if (!isReady.value) {
      return;
    }
    if (options.interactiveUiHovering.value) {
      await setPassthrough(false);
      return;
    }
    /* Corners are often canvas-transparent; without this, passthrough stays on and grips never get events. */
    if (isPointerOverWindowCornerResizeZone(event.clientX, event.clientY)) {
      await setPassthrough(false);
      return;
    }
    const canvas = options.canvasRef.value;
    if (!canvas) {
      return;
    }
    const isTransparent = detectCanvasPixelTransparent(canvas, event.clientX, event.clientY);
    await setPassthrough(isTransparent);
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

function detectCanvasPixelTransparent(canvas: HTMLCanvasElement, clientX: number, clientY: number): boolean {
  const rect = canvas.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) {
    return false;
  }

  const normalizedX = (clientX - rect.left) / rect.width;
  const normalizedY = (clientY - rect.top) / rect.height;

  if (normalizedX < 0 || normalizedY < 0 || normalizedX > 1 || normalizedY > 1) {
    return true;
  }

  const gl = canvas.getContext("webgl2", { preserveDrawingBuffer: true }) ?? canvas.getContext("webgl", { preserveDrawingBuffer: true });
  if (!gl) {
    return false;
  }

  const pixelX = Math.floor(normalizedX * canvas.width);
  const pixelY = Math.floor((1 - normalizedY) * canvas.height);
  const alphaPixel = new Uint8Array(4);
  gl.readPixels(pixelX, pixelY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, alphaPixel);
  return alphaPixel[3] === 0;
}
