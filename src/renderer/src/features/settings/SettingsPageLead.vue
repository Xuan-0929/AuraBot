<template>
  <div class="settings-page-lead">
    <h2 class="settings-page-lead-title">{{ title }}</h2>
    <p class="settings-page-lead-hint">{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string;
  hint: string;
}>();
</script>
