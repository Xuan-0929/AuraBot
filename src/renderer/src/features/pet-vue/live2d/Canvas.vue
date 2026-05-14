<template>
  <div ref="hostRef" class="live2d-canvas-host" data-render-state="ready">
    <canvas ref="canvasRef" class="live2d-canvas" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { Application } from "pixi.js";
import { PET_LIVE2D_FALLBACK_SIZE } from "./live2dPlacement";
import { createLive2DResizeController, type Live2DResizeController } from "../../live2d/resizeController";

const emit = defineEmits<{
  ready: [{ app: Application; canvas: HTMLCanvasElement; host: HTMLDivElement }];
  resized: [{ width: number; height: number }];
}>();

const hostRef = ref<HTMLDivElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const appRef = ref<Application | null>(null);
let resizeController: Live2DResizeController | undefined;

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

  resizeController = createLive2DResizeController(host, PET_LIVE2D_FALLBACK_SIZE, (hostSize) => {
    app.renderer.resize(hostSize.width, hostSize.height);
    emit("resized", { width: hostSize.width, height: hostSize.height });
  });
  resizeController.resizeNow();
  emit("ready", { app, canvas, host });
});

onUnmounted(() => {
  resizeController?.dispose();
  resizeController = undefined;
  appRef.value?.destroy(true, { children: true, texture: true, baseTexture: true });
  appRef.value = null;
});
</script>
