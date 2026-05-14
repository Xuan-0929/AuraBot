<template>
  <Scene
    :pet-ready="true"
    :model-path="modelPath"
    :focus-at="{ x: 0, y: 0 }"
    @canvas-ready="() => undefined"
    @interactive-area-change="() => undefined"
    @model-ready="() => undefined"
  />
</template>

<script setup lang="ts">
import Scene from "../pet-vue/live2d/Scene.vue";

defineProps<{
  modelPath: string;
}>();
</script>
