<template>
  <div ref="hostRef" class="live2d-canvas-host" data-render-state="ready">
    <canvas ref="canvasRef" class="live2d-canvas" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { Application } from "pixi.js";
import { PET_LIVE2D_FALLBACK_SIZE, resolveLive2DHostSize } from "./live2dPlacement";

const emit = defineEmits<{
  ready: [{ app: Application; canvas: HTMLCanvasElement; host: HTMLDivElement }];
  resized: [{ width: number; height: number }];
}>();

const hostRef = ref<HTMLDivElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const appRef = ref<Application | null>(null);
let resizeObserver: ResizeObserver | undefined;
let detachWindowResize: (() => void) | undefined;

onMounted(() => {
  const host = hostRef.value;
  const canvas = canvasRef.value;
  if (!host || !canvas) {
    return;
  }

  const app = new Application({
    view: canvas,
    backgroundAlpha: 0,
    antialias: true,
    preserveDrawingBuffer: true,
    resolution: 2
  });
  appRef.value = app;

  const resize = () => {
    const hostSize = resolveLive2DHostSize(host, PET_LIVE2D_FALLBACK_SIZE);
    app.renderer.resize(hostSize.width, hostSize.height);
    emit("resized", { width: hostSize.width, height: hostSize.height });
    // Electron/window resize can lag layout one frame; align with React canvas retries.
    window.requestAnimationFrame(() => {
      const again = resolveLive2DHostSize(host, PET_LIVE2D_FALLBACK_SIZE);
      if (again.width !== hostSize.width || again.height !== hostSize.height) {
        app.renderer.resize(again.width, again.height);
        emit("resized", { width: again.width, height: again.height });
      }
    });
  };

  const onWindowResize = () => resize();

  resizeObserver = new ResizeObserver(() => resize());
  resizeObserver.observe(host);
  window.addEventListener("resize", onWindowResize);
  detachWindowResize = () => window.removeEventListener("resize", onWindowResize);
  resize();
  emit("ready", { app, canvas, host });
});

onUnmounted(() => {
  detachWindowResize?.();
  detachWindowResize = undefined;
  resizeObserver?.disconnect();
  resizeObserver = undefined;
  appRef.value?.destroy(true, { children: true, texture: true, baseTexture: true });
  appRef.value = null;
});
</script>
