<template>
  <div :class="['app-shell', `app-shell-${surface}`]">
    <ConsoleWindow v-if="surface === 'console'" />
    <SettingsWindow v-else-if="surface === 'settings'" />
    <PetApp v-else />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import ConsoleWindow from "./features/console/ConsoleWindow.vue";
import PetApp from "./features/pet-vue/PetApp.vue";
import SettingsWindow from "./features/settings/SettingsWindow.vue";
import { useAuraBotStore } from "./app/store";

type Surface = "console" | "settings" | "pet";

const store = useAuraBotStore();
const hash = ref(window.location.hash.toLowerCase());
let unsubscribeIpc: (() => void) | undefined;

const surface = computed<Surface>(() => {
  if (hash.value === "#console") {
    return "console";
  }
  if (hash.value === "#settings") {
    return "settings";
  }
  return "pet";
});

function handleHashChange() {
  hash.value = window.location.hash.toLowerCase();
}

onMounted(async () => {
  window.addEventListener("hashchange", handleHashChange);
  await store.loadInitialState();
  unsubscribeIpc = store.subscribeToPetEvents();
});

onUnmounted(() => {
  window.removeEventListener("hashchange", handleHashChange);
  unsubscribeIpc?.();
});
</script>
