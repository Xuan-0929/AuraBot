<template>
  <section class="desktop-pet-stage pet-vue-desktop-pet-stage">
    <div class="desktop-pet-stage-main">
      <Canvas @ready="handleCanvasReady" @resized="handleCanvasResized" />
      <Model
        v-if="petReady"
        :app="app"
        :model-path="modelPath"
        :focus-at="focusAt"
        :canvas-size="canvasSize"
        :pointer-at="pointerAt"
        :view-scale="viewScale"
        @interactive-change="(value) => emit('interactiveAreaChange', value)"
        @model-ready="() => emit('modelReady')"
      />
    </div>
    <slot />
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import type { Application } from "pixi.js";
import Canvas from "./Canvas.vue";
import Model from "./Model.vue";

defineProps<{
  petReady: boolean;
  modelPath: string;
  focusAt: { x: number; y: number };
  pointerAt?: { x: number; y: number };
  viewScale?: number;
}>();

const emit = defineEmits<{
  canvasReady: [HTMLCanvasElement];
  interactiveAreaChange: [boolean];
  modelReady: [];
}>();

const app = ref<Application | null>(null);
const canvasSize = ref({ width: 1, height: 1 });

function handleCanvasReady(payload: { app: Application; canvas: HTMLCanvasElement }) {
  app.value = payload.app;
  emit("canvasReady", payload.canvas);
}

function handleCanvasResized(payload: { width: number; height: number }) {
  canvasSize.value = payload;
}
</script>
