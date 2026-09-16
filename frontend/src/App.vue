<!-- Selects Pet, console, settings or the independent Live debug viewer. Only ordinary
     surfaces initialize chat history and Pet events; debug uses its own bounded desktop feed.
     Window events come from Electron; no model Context or business lifecycle is built here.
     The macOS class lets shared styles reserve native window controls and provide drag regions
     for the hidden title bars configured by main/createWindow. -->
<template>
  <div :class="['app-shell', `app-shell-${surface}`, { 'macos-window': isMacOS }]">
    <ConsoleWindow v-if="surface === 'console'" />
    <SettingsWindow v-else-if="surface === 'settings'" />
    <LiveDebugWindow v-else-if="surface === 'live-debug'" />
    <PetApp v-else />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import LiveDebugWindow from "./features/live-debug/LiveDebugWindow.vue";
import ConsoleWindow from "./features/console/ConsoleWindow.vue";
import PetApp from "./features/pet-vue/PetApp.vue";
import SettingsWindow from "./features/settings/SettingsWindow.vue";
import { useAuraBotStore } from "./app/store";

type Surface = "console" | "settings" | "pet" | "live-debug";

const store = window.location.hash.toLowerCase() === "#live-debug" ? undefined : useAuraBotStore();
const isMacOS = navigator.userAgent.includes("Macintosh");
const hash = ref(window.location.hash.toLowerCase());
let unsubscribeIpc: (() => void) | undefined;

const surface = computed<Surface>(() => {
  if (hash.value === "#live-debug") return "live-debug";
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
  if (!store || surface.value === "live-debug") return;
  await store.initialize();
  unsubscribeIpc = store.subscribeToPetEvents();
});

onUnmounted(() => {
  window.removeEventListener("hashchange", handleHashChange);
  unsubscribeIpc?.();
});
</script>
