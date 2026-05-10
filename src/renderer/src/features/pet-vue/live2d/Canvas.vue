<template>
  <div ref="hostRef" class="live2d-canvas-host" data-render-state="ready">
    <canvas ref="canvasRef" class="live2d-canvas" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { Application } from "pixi.js";

const emit = defineEmits<{
  ready: [{ app: Application; canvas: HTMLCanvasElement; host: HTMLDivElement }];
  resized: [{ width: number; height: number }];
}>();

const hostRef = ref<HTMLDivElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);
const appRef = ref<Application | null>(null);
let resizeObserver: ResizeObserver | undefined;

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
    const width = Math.max(1, Math.round(host.clientWidth));
    const height = Math.max(1, Math.round(host.clientHeight));
    app.renderer.resize(width, height);
    emit("resized", { width, height });
  };

  resizeObserver = new ResizeObserver(() => resize());
  resizeObserver.observe(host);
  resize();
  emit("ready", { app, canvas, host });
});

onUnmounted(() => {
  resizeObserver?.disconnect();
  appRef.value?.destroy(true, { children: true, texture: true, baseTexture: true });
  appRef.value = null;
});
</script>
