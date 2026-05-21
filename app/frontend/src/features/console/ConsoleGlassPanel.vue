<template>
  <div :class="['console-glass-panel', `console-glass-panel-${variant}`]">
    <slot />
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    variant?: "panel" | "control" | "pill";
  }>(),
  { variant: "panel" }
);
</script>
