import { computed, onUnmounted, ref, type Ref } from "vue";

interface UseClickThroughOptions {
  enabled: Ref<boolean>;
  canvasRef: Ref<HTMLCanvasElement | null>;
  interactiveUiHovering: Ref<boolean>;
}

export function useClickThrough(options: UseClickThroughOptions) {
  const isPassthrough = ref<boolean | undefined>(undefined);
  const isReady = computed(() => options.enabled.value && Boolean(options.canvasRef.value));
  let pendingPointer: PointerEvent | undefined;
  let pointerFrameId: number | undefined;
  const pixelDetector = createCanvasPixelTransparencyDetector();

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
    pendingPointer = event;
    if (pointerFrameId !== undefined) {
      return;
    }
    pointerFrameId = window.requestAnimationFrame(() => {
      pointerFrameId = undefined;
      const nextPointer = pendingPointer;
      pendingPointer = undefined;
      const nextCanvas = options.canvasRef.value;
      if (!nextPointer || !nextCanvas) {
        return;
      }
      const isTransparent = pixelDetector.detect(nextCanvas, nextPointer.clientX, nextPointer.clientY);
      void setPassthrough(isTransparent);
    });
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
    if (pointerFrameId !== undefined) {
      window.cancelAnimationFrame(pointerFrameId);
      pointerFrameId = undefined;
    }
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

function createCanvasPixelTransparencyDetector() {
  let gl: WebGLRenderingContext | WebGL2RenderingContext | null = null;
  let glCanvas: HTMLCanvasElement | undefined;
  let lastPixelKey = "";
  let lastResult = false;
  const alphaPixel = new Uint8Array(4);

  return {
    detect(canvas: HTMLCanvasElement, clientX: number, clientY: number): boolean {
      const rect = canvas.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) {
        return false;
      }

      const normalizedX = (clientX - rect.left) / rect.width;
      const normalizedY = (clientY - rect.top) / rect.height;

      if (normalizedX < 0 || normalizedY < 0 || normalizedX > 1 || normalizedY > 1) {
        return true;
      }

      if (!gl || glCanvas !== canvas) {
        glCanvas = canvas;
        gl = canvas.getContext("webgl2", { preserveDrawingBuffer: true }) ?? canvas.getContext("webgl", { preserveDrawingBuffer: true });
      }
      if (!gl) {
        return false;
      }

      const pixelX = Math.floor(normalizedX * canvas.width);
      const pixelY = Math.floor((1 - normalizedY) * canvas.height);
      const pixelKey = `${canvas.width}:${canvas.height}:${pixelX}:${pixelY}`;
      if (pixelKey === lastPixelKey) {
        return lastResult;
      }

      gl.readPixels(pixelX, pixelY, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, alphaPixel);
      lastPixelKey = pixelKey;
      lastResult = alphaPixel[3] === 0;
      return lastResult;
    }
  };
}
